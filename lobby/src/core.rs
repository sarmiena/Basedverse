//! The lobby's state, owned by one task (`Core::run`): the offices, their knocks and visits, the bans and the world
//! groups. Connections (conn.rs) bring it what their visit-clients say, one event at a time, in order; it answers
//! by sending each office's connection what it should hear. One owner and no locks, so every message is handled
//! against the lobby as the one before it left it, as the Python lobby's single thread had it.
//!
//! An office is a session: it outlives its connection by the resume window, so a visit-client that reconnects in
//! time is the same office, with its knocks and visits (../../visit/SPEC.md 6.1).
use std::{
    collections::{HashMap, HashSet, VecDeque},

    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use axum::extract::ws::Utf8Bytes;
use lobby_proto::{self as proto, About, Ban, FromLobby, Knocker, Knocking, Limits, LinkTo, Office, Peer, Sayer, Sim, ToLobby};

use serde_json::{json, Value};
use tokio::sync::{mpsc, oneshot};
use tracing::{info, warn};

use crate::groups::{Groups, Joining, Links};

/// Browsers an office may have in its lobby at once (3.1: linked browsers).
const WEB_SESSIONS: usize = 4;
/// Browsers waiting with a pairing code, at most: lobby-wide, and from one address (an IPv6 one: its /64).
const MOST_PENDING: usize = 5000;
const PENDING_PER_IP: usize = 4;
/// Web passes remembered as used (until they expire), at most.
const MOST_PASSES: usize = 20_000;
/// Passwords tried or set (each a slow hash), and pairing codes typed, a minute, by one office.
const PASSWORDS_A_MINUTE: usize = 5;
const CODES_A_MINUTE: usize = 5;
/// Passwords hashed at once, at most (each Argon2id: some 19 MiB and a processor's tens of milliseconds); and wrong
/// passwords one world takes an hour, from everyone together (keys are free to make: a world's guard is its own).
const HASHING_AT_ONCE: usize = 4;
const WRONG_PER_WORLD_HOUR: usize = 30;

/// What a connection is told to do.
pub enum Out {
    Text(Utf8Bytes),
    Close(u16, &'static str),
}

/// A connection, as the core reaches it. What waits to go to it is bounded (`QUEUE`): a connection that cannot take
/// what it is sent (a client that stopped reading) is closed, never queued for without end.
#[derive(Clone)]
pub struct Conn {
    pub id: u64,
    pub tx: mpsc::Sender<Out>,
    pub slow: std::sync::Arc<tokio::sync::Notify>,
}

/// Messages waiting for one connection, at most.
pub const QUEUE: usize = 1024;

impl Conn {
    fn put(&self, o: Out) {
        if let Err(mpsc::error::TrySendError::Full(_)) = self.tx.try_send(o) {
            self.slow.notify_one();
        }
    }
    pub fn send(&self, m: &FromLobby) {
        self.put(Out::Text(m.text().into()));
    }
    fn send_text(&self, t: &Utf8Bytes) {
        self.put(Out::Text(t.clone()));
    }
    pub fn close(&self, code: u16, why: &'static str) {
        self.put(Out::Close(code, why));
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Proto {
    V3,
    V4,
}

impl Proto {
    pub fn name(self) -> &'static str {
        match self {
            Proto::V3 => proto::PROTO3,
            Proto::V4 => proto::PROTO4,
        }
    }
}

/// What the core is asked.
pub enum Ev {
    /// A hello (version 4: its key already proved, `key`), on `conn`. The reply is the session's id, or none after
    /// an error said on `conn` (the connection may try again).
    Hello { conn: Conn, proto: Proto, hello: Value, key: Option<String>, reply: oneshot::Sender<Option<String>> },
    /// A message from an office's connection, after its hello.
    Msg { conn: u64, office: String, m: Value },
    /// A connection closed: its office (if this is still its connection) is held for the resume window.
    Closed { conn: u64, office: String },
    /// 3.1: a browser, its own key proved (conn.rs), and the web pass it brought, if any (the office's key, until
    /// when, its nonce: its signature checked already). The reply: whether its connection goes on (linked, or waiting
    /// with a pairing code), else refused (said, and closed, on `conn`).
    /// `switch`: the page agreed to a pass that links this browser, linked to another office, to the pass's instead.
    BrowserHello { conn: Conn, key: String, agent: String, ip: std::net::IpAddr, pass: Option<(String, u64, String)>, switch: bool, reply: oneshot::Sender<bool> },
    BrowserMsg { conn: u64, m: Value },
    BrowserClosed { conn: u64 },
    /// A pairing code's time is up.
    CodeExpired(String),
    /// A password hashed, or checked, off this task (slow on purpose): the message it was for, again, with it.
    Password { oid: String, key: String, replying: Option<Conn>, m: ToLobby, pw: Pw },
    KnockExpired(String),
    HoldExpired(String, u64),
    /// For /health: the offices in the lobby, every version.
    Count(oneshot::Sender<usize>),
    /// For the public page: the rooms that describe themselves, the groups, how many are online (version 4).
    Public(oneshot::Sender<Value>),
    /// Whether a key may use the relay: it has a session, version 4 (the key proved).
    RelayAccess(String, oneshot::Sender<bool>),
    /// The test aid (--debug): the state, as JSON; a connection dropped without a word; every session ended; knocks
    /// that lie about the knocker's key.
    DebugState(oneshot::Sender<Value>),
    DebugDrop(String, oneshot::Sender<bool>),
    DebugReset(oneshot::Sender<()>),
    DebugLie(Option<String>),
}

/// What a run is given.
pub struct Config {
    pub knock_ttl: u64,
    /// Seconds a dropped session is kept.
    pub resume: u64,
    /// The relays visit-clients are told to use; none: their own.
    pub relays: Vec<String>,
    /// Keys that are the lobby's operators.
    pub admins: HashSet<String>,
    pub title: Option<String>,
    pub motd: Option<String>,
    /// The members a world takes.
    pub world_size: usize,
}

/// A password's work, done off the core's task: hashed (none: it could not be), or checked (against this hash).
pub enum Pw {
    Hashed(Option<String>),
    Right(bool, String),
}

enum PwJob {
    Hash(String),
    Check(String, String),
}

/// A browser's connection (3.1): its key, what it says it is, where from, and whom it speaks for, once linked (else
/// its code); `gone`: unlinked, closing: nothing more of it is taken.
struct Browser {
    conn: Conn,
    key: String,
    agent: String,
    ip: std::net::IpAddr,
    office: Option<String>,
    code: Option<String>,
    gone: bool,
}

/// An address as pairing codes count it: an IPv4 one, or an IPv6 one's /64 (one host has a whole /64).
fn bucket(ip: std::net::IpAddr) -> std::net::IpAddr {
    match ip {
        std::net::IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => v4.into(),
            None => {
                let s = v6.segments();
                std::net::Ipv6Addr::new(s[0], s[1], s[2], s[3], 0, 0, 0, 0).into()
            }
        },
        v4 => v4,
    }
}

const STATUSES: [&str; 2] = ["available", "busy"];
/// Groups one office may make in an hour.
const GROUPS_AN_HOUR: usize = 3;
const END_REASONS: [&str; 3] = ["left", "kicked", "link_failed"];

struct Session {
    id: String,
    proto: Proto,
    conn: Option<Conn>,
    name: String,
    status: String,
    sim: Sim,
    max_visitors: u8,
    resume: String,
    /// {endpoint, addr} once published, until withdrawn.
    link: Option<LinkTo>,
    /// Version 4: the key it proved. Version 3: the endpoint it last published, kept while its link is withdrawn.
    key: Option<String>,
    world: bool,
    links: bool,
    chat: bool,
    whisper: bool,
    about: Option<About>,
    admin: bool,
    said: VecDeque<Instant>,
    visiting: Option<String>,
    hosting: HashSet<String>,
    watching: HashSet<String>,
    watched: HashSet<String>,
    knocked: VecDeque<Instant>,
    knocked_watch: VecDeque<Instant>,
    /// When it last made worlds (a few an hour at most), tried or set a password, typed a pairing code.
    made: VecDeque<Instant>,
    passwords: VecDeque<Instant>,
    codes: VecDeque<Instant>,
    /// Counts holds: a hold's expiry that finds another number was cancelled.
    held: u64,
    holding: bool,
    /// 3.1: its linked browsers in the lobby now (the lobby's page, as this office).
    web: Vec<Conn>,
}

struct Knock {
    id: String,
    from: String,
    to: String,
    message: String,
    until: Instant,
    watch: bool,
    link: bool,
}

struct Visit {
    id: String,
    host: String,
    visitor: String,
    ticket: String,
    watch: bool,
    link: bool,
}

pub struct Core {
    cfg: Config,
    limits3: Limits,
    limits4: Limits,
    me: mpsc::UnboundedSender<Ev>,
    offices: HashMap<String, Session>,
    by_name: HashMap<String, String>,
    by_key: HashMap<String, String>,
    resumes: HashMap<String, String>,
    knocks: HashMap<String, Knock>,
    /// The order knocks were made in, for a resumed office's replay.
    knock_order: Vec<String>,
    visits: HashMap<String, Visit>,
    visit_order: Vec<String>,
    bans: crate::groups::Bans,
    groups: Groups,
    lie: Option<String>,
    /// The connection whose message is being handled, when a browser's: its refusals are said to it.
    replying: Option<Conn>,
    /// Web passes used (nonce, until when): each once.
    passes: HashMap<String, u64>,
    /// 3.1: linked browsers (kept), browsers connected (by connection), and those waiting by their pairing codes.
    links: Links,
    browsers: HashMap<u64, Browser>,
    pending: HashMap<String, u64>,
    pending_from: HashMap<std::net::IpAddr, usize>,
    /// Passwords being hashed now; and each world's wrong passwords, lately (by its name in lower case).
    hashing: usize,
    wrong: HashMap<String, VecDeque<Instant>>,
}

/// `n` random bytes, as lower-case hex.
pub fn hex(n: usize) -> String {
    let mut b = vec![0u8; n];
    getrandom::fill(&mut b).expect("the OS's random numbers");
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// A pairing code: 8 of `CODE_ALPHABET` (32 of them: every byte maps evenly).
fn pairing_code() -> String {
    let mut b = [0u8; 8];
    getrandom::fill(&mut b).expect("the OS's random numbers");
    b.iter().map(|x| proto::CODE_ALPHABET[(x % 32) as usize] as char).collect()
}

/// Whether one more of these may be done now: at most `most` a minute.
fn within(times: &mut VecDeque<Instant>, most: usize) -> bool {
    let now = Instant::now();
    while times.front().is_some_and(|t| now.duration_since(*t) > Duration::from_secs(60)) {
        times.pop_front();
    }
    if times.len() >= most {
        return false;
    }
    times.push_back(now);
    true
}

pub fn now_unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

fn sim_of(v: Option<&Value>) -> Sim {
    let s = |k: &str| v.and_then(|v| v.get(k)).and_then(Value::as_str).map(|s| s.chars().take(40).collect());
    Sim { name: s("name"), version: s("version") }
}

fn ref_of(m: &Value) -> String {
    m.get("type").and_then(Value::as_str).unwrap_or("").to_string()
}

impl Core {
    pub fn new(cfg: Config, me: mpsc::UnboundedSender<Ev>, bans: crate::groups::Bans, groups: Groups, links: Links) -> Core {
        let (mut limits3, mut limits4) = (Limits::v3(cfg.knock_ttl), Limits::v4(cfg.knock_ttl));
        (limits3.resume, limits4.resume) = (cfg.resume, cfg.resume);
        limits4.group_members = Some(cfg.world_size);
        Core {
            cfg, limits3, limits4, me, offices: HashMap::new(), by_name: HashMap::new(), by_key: HashMap::new(), resumes: HashMap::new(),
            knocks: HashMap::new(), knock_order: Vec::new(), visits: HashMap::new(), visit_order: Vec::new(), bans, groups, lie: None, replying: None,
            passes: HashMap::new(), links, browsers: HashMap::new(), pending: HashMap::new(), pending_from: HashMap::new(), hashing: 0,
            wrong: HashMap::new(),
        }
    }

    pub async fn run(mut self, mut rx: mpsc::UnboundedReceiver<Ev>) {
        while let Some(ev) = rx.recv().await {
            match ev {
                Ev::Hello { conn, proto, hello, key, reply } => {
                    let id = self.hello(conn, proto, hello, key);
                    let _ = reply.send(id);
                }
                Ev::Msg { conn, office, m } => self.msg(conn, &office, m),
                Ev::Closed { conn, office } => self.closed(conn, &office),
                Ev::BrowserHello { conn, key, agent, ip, pass, switch, reply } => {
                    let on = self.browser_hello(conn, key, agent, ip, pass, switch);
                    let _ = reply.send(on);
                }
                Ev::BrowserMsg { conn, m } => self.browser_msg(conn, m),
                Ev::BrowserClosed { conn } => self.browser_closed(conn),
                Ev::CodeExpired(code) => {
                    if let Some(id) = self.pending.get(&code).copied() {
                        self.unpend(id);
                        let c = &self.browsers[&id].conn;
                        c.send(&FromLobby::error("code_expired", "this code's time is up: ask for a new one", None));
                        c.close(4408, "code expired");
                    }
                }
                Ev::Password { oid, key, replying, m, pw } => {
                    self.hashing = self.hashing.saturating_sub(1);
                    // Its office still the same, and, a browser's, that browser still in as it (not unlinked meanwhile).
                    let asker = replying.as_ref().is_none_or(|c| self.browsers.get(&c.id).is_some_and(|b| !b.gone && b.office.as_deref() == Some(oid.as_str())));
                    if asker && self.offices.get(&oid).is_some_and(|o| o.key.as_deref() == Some(key.as_str())) {
                        self.replying = replying;
                        self.on_group(&oid, m, Some(pw));
                        self.replying = None;
                    }
                }
                Ev::KnockExpired(kid) => self.knock_expired(&kid),
                Ev::HoldExpired(oid, n) => {
                    if self.offices.get(&oid).is_some_and(|o| o.holding && o.held == n) {
                        self.depart(&oid);
                    }
                }
                Ev::Count(r) => {
                    let _ = r.send(self.offices.len());
                }
                Ev::Public(r) => {
                    let _ = r.send(self.public());
                }
                Ev::RelayAccess(key, r) => {
                    let ok = self.by_key.get(&key).and_then(|id| self.offices.get(id)).is_some_and(|o| o.proto == Proto::V4);
                    let _ = r.send(ok);
                }
                Ev::DebugState(r) => {
                    let offices: Vec<Value> = self.offices.values().map(|o| json!({ "id": o.id, "name": o.name, "key": o.key,
                        "proto": o.proto.name(), "connected": o.conn.is_some(), "visiting": o.visiting, "hosting": o.hosting.len(),
                        "watching": o.watching.len(), "watched": o.watched.len() })).collect();
                    let by_name: serde_json::Map<String, Value> = self.offices.values().map(|o| (o.name.clone(), json!(o.id))).collect();
                    let _ = r.send(json!({ "offices": offices, "by_name": by_name, "visits": self.visits.len(), "knocks": self.knocks.len(),
                        "groups": self.groups.names(), "browsers": self.browsers.len(), "pending": self.pending.len() }));
                }
                Ev::DebugDrop(oid, r) => {
                    let c = self.offices.get(&oid).and_then(|o| o.conn.clone());
                    if let Some(c) = &c {
                        c.close(1011, "dropped (a test)");
                    }
                    let _ = r.send(c.is_some());
                }
                Ev::DebugReset(r) => {
                    for oid in self.offices.keys().cloned().collect::<Vec<_>>() {
                        if let Some(c) = self.offices.get(&oid).and_then(|o| o.conn.clone()) {
                            c.close(1001, "reset (a test)");
                        }
                        self.depart(&oid);
                    }
                    for b in self.browsers.values() {
                        b.conn.close(1001, "reset (a test)");
                    }
                    self.pending.clear();
                    self.pending_from.clear();
                    self.wrong.clear();
                    self.groups.reset();
                    self.bans.reset();
                    self.links.reset();
                    let _ = r.send(());
                }
                Ev::DebugLie(key) => self.lie = key,
            }
        }
    }

    fn limits(&self, p: Proto) -> &Limits {
        if p == Proto::V4 { &self.limits4 } else { &self.limits3 }
    }

    // ---------- sending ----------
    /// To an office: its own connection, and (3.1) the browsers speaking for it, what a browser is told (`for_web`).
    fn tell(&self, oid: &str, m: &FromLobby) {
        let Some(o) = self.offices.get(oid) else { return };
        if let Some(c) = &o.conn {
            c.send(m);
        }
        if !o.web.is_empty() && for_web(m) {
            let text: Utf8Bytes = m.text().into();
            for c in &o.web {
                c.send_text(&text);
            }
        }
    }

    /// The connections that hear an office's lines: its own, and its browsers'.
    fn hearers<'a>(&'a self, o: &'a Session) -> impl Iterator<Item = &'a Conn> {
        o.conn.iter().chain(o.web.iter())
    }

    /// A refusal, to whoever asked: the browser whose message this is, else the office's own connection.
    fn error(&self, oid: &str, code: &str, text: impl Into<String>, reference: Option<&str>) {
        self.answer(oid, &FromLobby::error(code, text, reference));
    }

    /// An answer (the group list, the bans, a refusal), to whoever asked alone.
    fn answer(&self, oid: &str, e: &FromLobby) {
        match &self.replying {
            Some(c) => c.send(e),
            None => {
                if let Some(c) = self.offices.get(oid).and_then(|o| o.conn.as_ref()) {
                    c.send(e);
                }
            }
        }
    }

    fn view(&self, p: Proto) -> FromLobby {
        let mut list: Vec<&Session> = self.offices.values().filter(|o| o.proto == p).collect();
        list.sort_by_key(|o| (o.name.to_ascii_lowercase(), o.id.clone()));
        let offices = list.into_iter().map(|o| Office {
            id: o.id.clone(), name: o.name.clone(), status: o.status.clone(), sim: o.sim.clone(), max_visitors: o.max_visitors,
            visitors: o.hosting.len(),
            // Version 4 says nobody's whereabouts to everyone (LOBBY.md 3): those they visit know.
            visiting: if p == Proto::V4 { None } else { o.visiting.as_ref().and_then(|v| self.visits.get(v)).map(|v| v.host.clone()) },
            ready: o.link.is_some(), key: o.key.clone(), watchers: o.watched.len(), world: o.world, links: o.links, whisper: o.whisper,
            about: if p == Proto::V4 { o.about.clone() } else { None },
        }).collect();
        FromLobby::Lobby { offices }
    }

    /// Everyone hears the lobby as it is now, each version its own.
    fn announce(&self) {
        for p in [Proto::V3, Proto::V4] {
            let conns: Vec<&Conn> = self.offices.values().filter(|o| o.proto == p).flat_map(|o| self.hearers(o)).collect();
            if conns.is_empty() {
                continue;
            }
            let text: Utf8Bytes = self.view(p).text().into();
            for c in conns {
                c.send_text(&text);
            }
        }
    }

    /// What the public page shows (LOBBY.md 2, rule 10): who is here and has written a line (no keys, nor where
    /// anyone is), the worlds with their sizes, and how many offices are in the lobby.
    fn public(&self) -> Value {
        let mut users: Vec<&Session> = self.offices.values().filter(|o| o.proto == Proto::V4 && o.about.is_some()).collect();
        users.sort_by_key(|o| o.name.to_ascii_lowercase());
        let users: Vec<Value> = users.into_iter().map(|o| json!({ "name": o.name, "sim": o.sim, "about": o.about, "status": o.status,
            "visitors": o.hosting.len(), "max_visitors": o.max_visitors, "ready": o.link.is_some() })).collect();
        let online = self.offices.values().filter(|o| o.proto == Proto::V4).count();
        json!({ "online": online, "users": users, "worlds": self.groups.listed(|k| self.online(k).is_some()) })
    }

    // ---------- hello ----------
    fn hello(&mut self, conn: Conn, p: Proto, m: Value, key: Option<String>) -> Option<String> {
        let refuse = |code: &str, text: &str| {
            conn.send(&FromLobby::error(code, text, Some("hello")));
            None
        };
        if let Some(k) = &key {
            if let Some(b) = self.bans.get(k) {
                let until = b.until.map_or("for good".to_string(), |u| format!("for {} more minutes", (u.saturating_sub(now_unix()) + 59) / 60));
                conn.send(&FromLobby::error("banned", format!("this key is banned from this lobby {until}{}", if b.reason.is_empty() { String::new() } else { format!(": {}", b.reason) }), Some("hello")));
                conn.close(4403, "banned");
                return None;
            }
        }
        // Resuming: the secret names a session of this version (and, version 4, of this key).
        let old = m.get("resume").and_then(Value::as_str).and_then(|r| self.resumes.get(r)).cloned()
            .filter(|oid| self.offices.get(oid).is_some_and(|o| o.proto == p && (p == Proto::V3 || o.key == key)));
        if let Some(oid) = old {
            let o = self.offices.get_mut(&oid).expect("resumed");
            if let Some(c) = o.conn.take() {
                if c.id != conn.id {
                    c.close(4000, "resumed on another connection");
                }
            }
            o.holding = false;
            o.held += 1;
            o.conn = Some(conn);
            self.welcome(&oid, true);
            self.tell(&oid, &self.view(p));
            for kid in self.knock_order.clone() {
                if self.knocks.get(&kid).is_some_and(|k| k.to == oid) {
                    self.tell(&oid, &self.knock_msg(&kid));
                }
            }
            for vid in self.visit_order.clone() {   // again, in case one was agreed while it was away
                if self.visits.get(&vid).is_some_and(|v| v.host == oid || v.visitor == oid) {
                    self.tell(&oid, &self.visit_msg(&vid, &oid));
                }
            }
            self.groups_on_welcome(&oid);
            info!("~ {} resumed", self.offices[&oid].name);
            return Some(oid);
        }
        let name = m.get("name").and_then(Value::as_str).unwrap_or("");
        if !proto::name_ok(name) {
            return refuse("name_invalid", "a name is 1-24 letters, digits, spaces, _ . -, starting and ending with a letter or digit");
        }
        // The name taken by another office (its own key's old session gives it back, below).
        let own = key.as_ref().and_then(|k| self.by_key.get(k)).cloned();
        if self.by_name.get(&name.to_ascii_lowercase()).is_some_and(|o| Some(o) != own.as_ref()) {
            return refuse("name_taken", &format!("{name} is taken"));
        }
        let status = m.get("status").map_or(Some("available"), Value::as_str);
        let max_visitors = match m.get("max_visitors") {
            None => Some(3),
            Some(v) => v.as_u64().filter(|n| *n <= self.limits(p).max_visitors as u64).map(|n| n as u8),
        };
        let sim_ok = m.get("sim").is_none_or(Value::is_object);
        let (Some(status), Some(max_visitors), true) = (status.filter(|s| STATUSES.contains(s)), max_visitors, sim_ok) else {
            return refuse("bad_message", &format!("status must be available or busy, sim an object, max_visitors 0 to {}", self.limits(p).max_visitors));
        };
        let about = match (p, m.get("about")) {
            (Proto::V4, Some(a)) if !a.is_null() => match self.about_of(a) {
                Ok(a) => Some(a),
                Err(e) => return refuse("bad_message", &e),
            },
            _ => None,
        };
        // Version 4: a key back in the lobby while its old session is still held, or still connected (a restart
        // quicker than its old connection was seen to die): the old one ends first, for good. Only now, the hello
        // found good: a refused one ends nothing.
        if let Some(old) = own {
            if let Some(c) = self.offices.get(&old).and_then(|o| o.conn.clone()) {
                c.close(4000, "replaced: this key said hello again");
            }
            self.depart(&old);
        }
        let id = loop {
            let id = format!("o{}", hex(4));
            if !self.offices.contains_key(&id) {
                break id;
            }
        };
        let flag = |k: &str| p == Proto::V4 || m.get(k) == Some(&Value::Bool(true));
        let admin = key.as_ref().is_some_and(|k| self.cfg.admins.contains(k));
        let s = Session {
            id: id.clone(), proto: p, conn: Some(conn), name: name.to_string(), status: status.to_string(), sim: sim_of(m.get("sim")),
            max_visitors, resume: String::new(), link: None, key: key.clone(), world: flag("world"), links: flag("links"), chat: flag("chat"),
            whisper: flag("whisper"), about, admin,
            said: VecDeque::new(), visiting: None, hosting: HashSet::new(), watching: HashSet::new(), watched: HashSet::new(),
            knocked: VecDeque::new(), knocked_watch: VecDeque::new(), made: VecDeque::new(), passwords: VecDeque::new(), codes: VecDeque::new(),
            held: 0, holding: false, web: Vec::new(),
        };
        self.by_name.insert(name.to_ascii_lowercase(), id.clone());
        if let Some(k) = &key {
            self.by_key.insert(k.clone(), id.clone());
            self.groups.seen(k, name);
        }
        self.offices.insert(id.clone(), s);
        self.welcome(&id, false);
        self.announce();
        self.groups_on_welcome(&id);
        info!("+ {name}, {} ({} online)", p.name(), self.offices.len());
        Some(id)
    }

    /// A fresh resume secret each time (the last one is spent); the session's visits and its own knocks waiting, so a
    /// visit-client back from a drop can end any that ended while it was away.
    fn welcome(&mut self, oid: &str, resumed: bool) {
        let fresh = hex(16);
        let o = self.offices.get_mut(oid).expect("welcomed");
        let old = std::mem::replace(&mut o.resume, fresh.clone());
        self.resumes.remove(&old);
        self.resumes.insert(fresh.clone(), oid.to_string());
        let o = &self.offices[oid];
        let visits = self.visit_order.iter().filter(|v| self.visits.get(*v).is_some_and(|v| v.host == oid || v.visitor == oid)).cloned().collect();
        let knocking = self.knock_order.iter().filter_map(|k| self.knocks.get(k)).filter(|k| k.from == oid)
            .map(|k| Knocking { id: k.id.clone(), to: k.to.clone() }).collect();
        let v4 = o.proto == Proto::V4;
        let w = FromLobby::Welcome {
            proto: o.proto.name().into(), id: o.id.clone(), name: o.name.clone(), key: if v4 { o.key.clone() } else { None },
            relays: self.cfg.relays.clone(), resume: fresh, resumed, visits, knocking, limits: self.limits(o.proto).clone(), chat: true,
            admin: o.admin, title: if v4 { self.cfg.title.clone() } else { None }, motd: if v4 { self.cfg.motd.clone() } else { None },
        };
        self.tell(oid, &w);
    }

    /// A session ends for good: its knocks are withdrawn, its visits end `gone`.
    fn depart(&mut self, oid: &str) {
        let Some(me) = self.offices.remove(oid) else { return };
        for c in &me.web {   // its browsers: it is gone, and so are they (they come back with it)
            c.send(&FromLobby::error("office_gone", format!("{} has left the lobby: this page comes back in when it does", me.name), None));
            c.close(4410, "its office left the lobby");
        }
        self.by_name.remove(&me.name.to_ascii_lowercase());
        self.resumes.remove(&me.resume);
        if let Some(k) = &me.key {
            if self.by_key.get(k).map(String::as_str) == Some(oid) {
                self.by_key.remove(k);
            }
        }
        for kid in self.knock_order.clone() {
            let Some(k) = self.knocks.get(&kid) else { continue };
            if k.from != oid && k.to != oid {
                continue;
            }
            let k = self.remove_knock(&kid).expect("there");
            if k.from == oid && self.offices.contains_key(&k.to) {
                self.tell(&k.to, &FromLobby::KnockCancelled { id: kid.clone(), reason: "gone".into() });
            }
            if k.to == oid && self.offices.contains_key(&k.from) {
                self.tell(&k.from, &answered(&k, false, Some("gone"), ""));
            }
        }
        for vid in self.visit_order.clone() {
            if self.visits.get(&vid).is_some_and(|v| v.host == oid || v.visitor == oid) {
                self.end_visit(&vid, "gone");
            }
        }
        self.announce();
        info!("- {} ({} online)", me.name, self.offices.len());
    }

    fn closed(&mut self, conn: u64, oid: &str) {
        let resume = self.limits3.resume;
        let Some(o) = self.offices.get_mut(oid) else { return };
        if o.conn.as_ref().is_none_or(|c| c.id != conn) {
            return;   // resumed on another connection meanwhile, or gone
        }
        o.conn = None;
        o.holding = true;
        o.held += 1;
        let (n, me, id) = (o.held, self.me.clone(), oid.to_string());
        info!("? {} dropped; held {resume}s", o.name);
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_secs(resume)).await;
            let _ = me.send(Ev::HoldExpired(id, n));
        });
    }

    // ---------- browsers (3.1: SPEC 6.11) ----------
    /// A browser, its own key proved: linked by the web pass it brought (its office's, checked by conn.rs: good once,
    /// within its time, its office in the lobby); else linked already; else it waits with a pairing code, for its
    /// office to type in its room. A linked browser speaks for its office while the office is in the lobby.
    fn browser_hello(&mut self, conn: Conn, key: String, agent: String, ip: std::net::IpAddr, pass: Option<(String, u64, String)>, switch: bool) -> bool {
        let refuse = |code: &str, text: String, close: u16, why: &'static str| {
            conn.send(&FromLobby::error(code, text, Some("hello")));
            conn.close(close, why);
            false
        };
        let now = now_unix();
        if self.passes.len() >= MOST_PASSES / 2 {
            self.passes.retain(|_, until| *until > now);
        }
        let office = match pass {
            Some((office, expires, nonce)) => {
                if expires <= now || expires > now + proto::PASS_TTL + 60 {
                    return refuse("bad_proof", "this link has expired: open the lobby from your Visit panel again".into(), 4401, "pass expired");
                }
                if self.passes.contains_key(&nonce) {
                    return refuse("bad_proof", "this link was used already: open the lobby from your Visit panel again".into(), 4401, "pass used");
                }
                if self.online(&office).is_none() {
                    return refuse("not_in_lobby", "the workroom this link is from is not in the lobby now".into(), 4410, "not in the lobby");
                }
                // Linked to another office already: never moved by a link alone (one sent by someone else, say), only
                // when the page has asked its person and they agreed (`switch`). The pass is not spent meanwhile.
                if let Some(other) = self.links.office(&key).filter(|o| *o != office && !switch) {
                    let (was, to) = (self.name_by_key(&other), self.name_by_key(&office));
                    return refuse("linked_elsewhere", format!("This browser is {was}'s in the lobby; this link would make it {to}'s"), 4409, "linked elsewhere");
                }
                if self.passes.len() >= MOST_PASSES {
                    return refuse("busy", "too many links at once: try again in a while".into(), 1013, "busy");
                }
                self.passes.insert(nonce, expires);
                if self.links.office(&key).is_some_and(|o| o != office) {
                    self.detach_key(&key, "this browser was linked to another workroom");   // its other tabs, the old office's
                }
                let gone = self.links.add(&key, &office, &agent, self.limits4.browsers.unwrap_or(10));
                for k in gone {
                    self.detach_key(&k, "unlinked: its workroom linked a newer browser");
                }
                Some(office)
            }
            None => self.links.office(&key),
        };
        let id = conn.id;
        self.browsers.insert(id, Browser { conn: conn.clone(), key: key.clone(), agent, ip, office: None, code: None, gone: false });
        match office {
            Some(office) => match self.online(&office).map(str::to_string) {
                Some(oid) => {
                    let on = self.attach(id, &oid);
                    if !on {
                        self.browsers.remove(&id);   // refused, closed: its connection says no more (never in)
                    }
                    on
                }
                None => {
                    self.browsers.remove(&id);
                    refuse("not_in_lobby", "the workroom this browser is linked to is not in the lobby now: it comes in when the workroom does".into(), 4410,
                           "its office is not in the lobby")
                }
            },
            None => {
                let from = bucket(ip);
                if self.pending.len() >= MOST_PENDING || self.pending_from.get(&from).copied().unwrap_or(0) >= PENDING_PER_IP {
                    self.browsers.remove(&id);
                    return refuse("busy", "too many browsers are waiting to be linked: try again in a while".into(), 1013, "busy");
                }
                let code = loop {
                    let c = pairing_code();
                    if !self.pending.contains_key(&c) {
                        break c;
                    }
                };
                self.pending.insert(code.clone(), id);
                *self.pending_from.entry(from).or_insert(0) += 1;
                self.browsers.get_mut(&id).expect("there").code = Some(code.clone());
                conn.send(&FromLobby::BrowserCode { code: code.clone(), pictures: proto::pictures(&key), expires_in: proto::PASS_TTL });
                let me = self.me.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(Duration::from_secs(proto::PASS_TTL)).await;
                    let _ = me.send(Ev::CodeExpired(code));
                });
                true
            }
        }
    }

    /// The name an office goes by: in the lobby now, else as its worlds last knew it, else "another workroom".
    fn name_by_key(&self, key: &str) -> String {
        self.by_key.get(key).and_then(|id| self.offices.get(id)).map(|o| o.name.clone()).unwrap_or_else(|| "another workroom".into())
    }

    /// A browser's code, no longer waiting (linked, expired, or gone).
    fn unpend(&mut self, id: u64) {
        let Some(b) = self.browsers.get_mut(&id) else { return };
        let Some(code) = b.code.take() else { return };
        self.pending.remove(&code);
        let from = bucket(b.ip);
        if let Some(n) = self.pending_from.get_mut(&from) {
            *n -= 1;
            if *n == 0 {
                self.pending_from.remove(&from);
            }
        }
    }

    /// Every connection of a browser's key out of the lobby at once (unlinked, or linked elsewhere now): told why,
    /// closed (4403), off its office's list of browsers in the lobby; nothing more of it is taken, nor said to it.
    fn detach_key(&mut self, key: &str, why: &str) {
        let ids: Vec<u64> = self.browsers.iter().filter(|(_, b)| b.key == key && !b.gone).map(|(id, _)| *id).collect();
        let mut told = HashSet::new();
        for id in ids {
            self.unpend(id);
            let b = self.browsers.get_mut(&id).expect("there");
            b.gone = true;
            b.conn.send(&FromLobby::error("unlinked", why, None));
            b.conn.close(4403, "unlinked");
            if let Some(oid) = b.office.take() {
                if let Some(o) = self.offices.get_mut(&oid) {
                    o.web.retain(|c| c.id != id);
                }
                told.insert(oid);
            }
        }
        for oid in told {
            self.tell_browsers(&oid);
        }
    }

    /// A linked browser in, as its office: welcomed, the lobby and the office's worlds; its office hears its list.
    fn attach(&mut self, id: u64, oid: &str) -> bool {
        let b = self.browsers.get_mut(&id).expect("a browser");
        if self.offices[oid].web.len() >= WEB_SESSIONS {
            b.conn.send(&FromLobby::error("too_many", format!("at most {WEB_SESSIONS} browsers in the lobby for one workroom at once"), Some("hello")));
            b.conn.close(4429, "too many browsers");
            return false;
        }
        b.office = Some(oid.to_string());
        self.unpend(id);
        let b = &self.browsers[&id];
        let (conn, key, agent) = (b.conn.clone(), b.key.clone(), b.agent.clone());
        self.links.used(&key, &agent);
        self.offices.get_mut(oid).expect("there").web.push(conn.clone());
        let o = &self.offices[oid];
        conn.send(&FromLobby::WebWelcome {
            proto: proto::PROTO4.into(), id: o.id.clone(), name: o.name.clone(), key: o.key.clone().unwrap_or_default(), limits: self.limits4.clone(),
            admin: o.admin, title: self.cfg.title.clone(), motd: self.cfg.motd.clone(),
        });
        conn.send(&self.view(Proto::V4));
        if let Some(k) = &o.key {
            for g in self.groups.of(k) {
                conn.send(&self.groups.view(&g, k));
            }
        }
        info!("+ a browser for {} ({agent})", o.name);
        self.tell_browsers(oid);
        true
    }

    fn browser_closed(&mut self, id: u64) {
        self.unpend(id);
        let Some(b) = self.browsers.remove(&id) else { return };
        if let Some(oid) = &b.office {
            if let Some(o) = self.offices.get_mut(oid) {
                o.web.retain(|c| c.id != id);
                self.tell_browsers(oid);
            }
        }
    }

    /// An office's linked browsers, to its own connection (as one comes, goes, is linked or unlinked); `answer`: to
    /// whoever asked.
    fn browsers_of(&self, oid: &str) -> FromLobby {
        let key = self.offices[oid].key.clone().unwrap_or_default();
        let on: HashSet<&str> = self.offices[oid].web.iter().filter_map(|c| self.browsers.get(&c.id)).map(|b| b.key.as_str()).collect();
        FromLobby::Browsers { browsers: self.links.of(&key, |k| on.contains(k)) }
    }

    fn tell_browsers(&self, oid: &str) {
        if let Some(c) = self.offices.get(oid).and_then(|o| o.conn.as_ref()) {
            c.send(&self.browsers_of(oid));
        }
    }

    /// A browser's message: linked, what a browser may do for its office (talk, worlds, the list, knock to walk into
    /// a room or link), never what lets anyone into its room (answering, its door) or moves its visits; refusals to it.
    /// Waiting with a code: nothing but bye.
    fn browser_msg(&mut self, id: u64, m: Value) {
        let Some(b) = self.browsers.get(&id).filter(|b| !b.gone) else { return };
        let (c, t) = (b.conn.clone(), ref_of(&m));
        let Some(oid) = b.office.clone().filter(|o| self.offices.contains_key(o)) else {
            if t == "bye" {
                return c.close(1000, "bye");
            }
            return c.send(&FromLobby::error("not_linked", "this browser is not linked yet: type its code in your room (Visit panel: Link a browser)", Some(&t)));
        };
        const WEB: [&str; 17] = ["chat", "whisper", "group_say", "group_create", "group_join", "group_leave", "group_remove", "group_unban", "group_set",
                                 "group_owner", "group_delete", "groups", "about", "admin", "knock", "bye", "browser_forget"];
        if !WEB.contains(&t.as_str()) {
            return c.send(&FromLobby::error("not_from_web", format!("{t} is your workroom's to do, from its room"), Some(&t)));
        }
        if t == "bye" {
            return c.close(1000, "bye");
        }
        if t == "browser_forget" {   // unlinked by itself: its key forgotten here, as it forgets it; its other tabs out too
            let b = self.browsers.get_mut(&id).expect("there");
            b.gone = true;
            b.office = None;
            let key = b.key.clone();
            self.offices.get_mut(&oid).expect("there").web.retain(|x| x.id != id);
            self.links.remove(&key, None);
            c.close(1000, "unlinked");
            self.detach_key(&key, "this browser unlinked itself");
            return self.tell_browsers(&oid);
        }
        if t == "knock" && (m.get("watch") == Some(&Value::Bool(true))) {
            return c.send(&FromLobby::error("not_from_web", "watching is your workroom's own (its world)", Some("knock")));
        }
        self.replying = Some(c);
        let conn_id = self.offices[&oid].conn.as_ref().map(|c| c.id);
        match conn_id {
            // As the office's own message, its refusals to this browser: the office need not be connected (held).
            Some(cid) => self.msg(cid, &oid, m),
            None => self.msg_held(&oid, m),
        }
        self.replying = None;
    }

    /// An office's own: the browser waiting with a code (`allow`: linked, and in), its linked browsers, one unlinked.
    fn on_browser(&mut self, oid: &str, m: ToLobby) {
        let r = match &m {
            ToLobby::BrowserCode { .. } => "browser_code",
            ToLobby::BrowserAllow { .. } => "browser_allow",
            ToLobby::BrowserRevoke { .. } => "browser_revoke",
            _ => "browsers",
        };
        let Some(okey) = self.offices[oid].key.clone() else { return };
        match m {
            ToLobby::BrowserCode { code } | ToLobby::BrowserAllow { code } => {
                let allow = r == "browser_allow";
                if !within(&mut self.offices.get_mut(oid).expect("here").codes, CODES_A_MINUTE) {
                    return self.error(oid, "rate_limited", format!("at most {CODES_A_MINUTE} codes a minute"), Some(r));
                }
                let Some(code) = code.as_deref().and_then(proto::code_of) else {
                    return self.error(oid, "bad_message", "a code is 8 letters and digits, as the lobby's page shows it", Some(r));
                };
                let Some(id) = self.pending.get(&code).copied() else {
                    return self.error(oid, "not_found", "no browser waits with that code (a code lasts 10 minutes: the page shows a new one)", Some(r));
                };
                let b = &self.browsers[&id];
                if !allow {
                    let p = FromLobby::BrowserPending { code: code.clone(), key: b.key.clone(), agent: b.agent.clone(), pictures: proto::pictures(&b.key) };
                    return self.answer(oid, &p);
                }
                let (key, agent) = (b.key.clone(), b.agent.clone());
                for k in self.links.add(&key, &okey, &agent, self.limits4.browsers.unwrap_or(10)) {
                    self.detach_key(&k, "unlinked: its workroom linked a newer browser");
                }
                if !self.attach(id, oid) {
                    self.tell_browsers(oid);
                }
            }
            ToLobby::BrowserRevoke { key } => {
                let Some(key) = key.filter(|k| self.links.remove(k, Some(&okey))) else {
                    return self.error(oid, "not_found", "no browser linked to this workroom with that key", Some(r));
                };
                let name = self.offices[oid].name.clone();
                self.detach_key(&key, &format!("{name} unlinked this browser"));
                self.tell_browsers(oid);
            }
            _ => self.answer(oid, &self.browsers_of(oid)),
        }
    }

    /// A browser's message while its office's connection is down (held for resuming): handled as the office's.
    fn msg_held(&mut self, oid: &str, m: Value) {
        let t = ref_of(&m);
        match serde_json::from_value::<ToLobby>(m) {
            Ok(x) => self.handle(oid, x),
            Err(_) => self.error(oid, "bad_message", format!("{t} is not as it should be"), Some(&t)),
        }
    }

    // ---------- messages ----------
    fn msg(&mut self, conn: u64, oid: &str, m: Value) {
        if self.offices.get(oid).is_none_or(|o| o.conn.as_ref().is_none_or(|c| c.id != conn)) {
            return;   // a connection its office has left (resumed elsewhere)
        }
        let t = ref_of(&m);
        let p = self.offices[oid].proto;
        if t == "hello" {
            return self.error(oid, "already_hello", format!("this connection is already in the lobby as {}", self.offices[oid].name), Some("hello"));
        }
        let msg: ToLobby = match serde_json::from_value(m) {
            Ok(x) => x,
            Err(_) if is_type(&t) => return self.error(oid, "bad_message", format!("{t} is not as it should be"), Some(&t)),
            Err(_) => return self.error(oid, "unknown_type", format!("no message type {t:?}"), Some(&t)),
        };
        if p == Proto::V3 && !matches!(msg, ToLobby::Link { .. } | ToLobby::Status { .. } | ToLobby::MaxVisitors { .. } | ToLobby::Knock { .. }
            | ToLobby::Answer { .. } | ToLobby::End { .. } | ToLobby::Rename { .. } | ToLobby::Chat { .. } | ToLobby::Whisper { .. } | ToLobby::Bye | ToLobby::Hello { .. }) {
            return self.error(oid, "unknown_type", format!("no message type {t:?}"), Some(&t));
        }
        self.handle(oid, msg);
    }

    /// A message from an office (or a browser speaking for it), its shape checked.
    fn handle(&mut self, oid: &str, msg: ToLobby) {
        let p = self.offices[oid].proto;
        match msg {
            ToLobby::Hello { .. } => self.error(oid, "already_hello", format!("this connection is already in the lobby as {}", self.offices[oid].name), Some("hello")),
            ToLobby::Prove { .. } => self.error(oid, "already_hello", "this connection has proved its key already", Some("prove")),
            ToLobby::Bye => {
                let c = self.offices[oid].conn.clone();
                self.depart(oid);
                if let Some(c) = c {
                    c.close(1000, "bye");
                }
            }
            ToLobby::Link { endpoint, addr } => self.on_link(oid, endpoint, addr),
            ToLobby::Status { status } => match status.filter(|s| STATUSES.contains(&s.as_str())) {
                Some(s) => {
                    self.offices.get_mut(oid).expect("here").status = s;
                    self.announce();
                }
                None => self.error(oid, "bad_message", "status must be one of available, busy", Some("status")),
            },
            ToLobby::MaxVisitors { n } => match n.filter(|n| *n <= self.limits(p).max_visitors) {
                Some(n) => {
                    self.offices.get_mut(oid).expect("here").max_visitors = n;   // lower than the visitors it has ends no visit
                    self.announce();
                }
                None => self.error(oid, "bad_message", format!("n must be 0 to {}", self.limits(p).max_visitors), Some("max_visitors")),
            },
            ToLobby::Knock { to, message, watch, link } => self.on_knock(oid, to, message, watch, link),
            ToLobby::Answer { knock, accept, message } => self.on_answer(oid, knock, accept, message),
            ToLobby::End { visit, reason } => self.on_end(oid, visit, reason),
            ToLobby::Rename { name } => self.on_rename(oid, name),
            ToLobby::Chat { text } => self.on_chat(oid, text),
            ToLobby::Whisper { to, text } => self.on_whisper(oid, to, text),
            ToLobby::About { about } => {
                let a = match about.map(|a| self.about_of(&serde_json::to_value(a).expect("json"))).transpose() {
                    Ok(a) => a,
                    Err(e) => return self.error(oid, "bad_message", e, Some("about")),
                };
                self.offices.get_mut(oid).expect("here").about = a;
                self.announce();
            }
            ToLobby::Door { .. } => {}   // 3.0's door open to anyone: public worlds since 3.1
            ToLobby::Admin { action, key, minutes, text } => self.on_admin(oid, action, key, minutes, text),
            m @ (ToLobby::BrowserCode { .. } | ToLobby::BrowserAllow { .. } | ToLobby::Browsers | ToLobby::BrowserRevoke { .. }) => self.on_browser(oid, m),
            ToLobby::BrowserForget => self.error(oid, "bad_message", "browser_forget is a linked browser's", Some("browser_forget")),
            g => self.on_group(oid, g, None),
        }
    }

    /// A room's line and tags, checked: text at most `about` characters (plain), at most `tags` tags, each `tag_ok`.
    fn about_of(&self, v: &Value) -> Result<About, String> {
        let l = &self.limits4;
        let (most, tags, tag) = (l.about.unwrap_or(200), l.tags.unwrap_or(5), l.tag.unwrap_or(20));
        let a: About = serde_json::from_value(v.clone()).map_err(|_| "about is { text, tags }".to_string())?;
        if a.tags.len() > tags {
            return Err(format!("at most {tags} tags"));
        }
        let text = proto::plain(&a.text, most);
        let mut seen = Vec::new();
        for t in a.tags {
            if !proto::tag_ok(&t) {
                return Err(format!("a tag is 1-{tag} lower-case letters, digits and -"));
            }
            if !seen.contains(&t) {
                seen.push(t);
            }
        }
        if seen.len() > tags {
            return Err(format!("at most {tags} tags"));
        }
        Ok(About { text, tags: seen })
    }

    fn on_link(&mut self, oid: &str, endpoint: Option<String>, addr: Option<Value>) {
        let max = self.limits3.link_addr;
        let me = self.offices.get_mut(oid).expect("here");
        if endpoint.is_none() && addr.is_none() {   // withdrawn (its page stopped hosting): not ready, until again
            me.link = None;
            return self.announce();
        }
        let (Some(endpoint), Some(addr)) = (endpoint.filter(|e| !e.is_empty() && e.len() <= 200), addr.filter(Value::is_object)) else {
            return self.error(oid, "bad_message", "link needs an endpoint (string) and an addr (object)", Some("link"));
        };
        if serde_json::to_string(&addr).map_or(usize::MAX, |s| s.len()) > max {
            return self.error(oid, "bad_message", format!("addr is over {max} bytes"), Some("link"));
        }
        let me = self.offices.get_mut(oid).expect("here");
        if me.proto == Proto::V4 && me.key.as_deref() != Some(endpoint.as_str()) {
            return self.error(oid, "bad_message", "the endpoint is the key this office proved", Some("link"));
        }
        me.key = Some(endpoint.clone());
        me.link = Some(LinkTo { endpoint, addr });
        self.announce();
    }

    fn remove_knock(&mut self, kid: &str) -> Option<Knock> {
        let k = self.knocks.remove(kid)?;
        self.knock_order.retain(|x| x != kid);
        Some(k)
    }

    fn knock_expired(&mut self, kid: &str) {
        let Some(k) = self.remove_knock(kid) else { return };
        if self.offices.contains_key(&k.from) {
            self.tell(&k.from, &answered(&k, false, Some("timeout"), ""));
        }
        if self.offices.contains_key(&k.to) {
            self.tell(&k.to, &FromLobby::KnockCancelled { id: kid.into(), reason: "timeout".into() });
        }
    }

    /// A knock to visit; to watch (`watch`, a world's: to see the door's plot from outside it); or to link (`link`,
    /// a knock its door answers that starts a watch, let in). A watch is not stopped by `busy` (the door's
    /// visit-client decides), counts toward max_watchers rather than max_visitors, and its errors say `ref: watch`.
    /// To a door that does not take links, a link is an ordinary knock.
    fn on_knock(&mut self, oid: &str, to: Option<String>, message: Option<String>, watch: bool, link: bool) {
        let r = if watch { "watch" } else { "knock" };
        let p = self.offices[oid].proto;
        let target = to.filter(|t| self.offices.get(t).is_some_and(|o| o.proto == p));
        let link = link && !watch && target.as_ref().is_some_and(|t| self.offices[t].links);
        let msg = message.unwrap_or_default();
        let l = self.limits(p).clone();
        if msg.chars().count() > l.message {
            return self.error(oid, "message_too_long", format!("message is over {} characters", l.message), Some(r));
        }
        let Some(tid) = target else { return self.error(oid, "not_found", "no office with that id", Some(r)) };
        let (me, t) = (&self.offices[oid], &self.offices[&tid]);
        let seen = watch || link;
        let refusal: Option<(&str, String)> = if tid == oid {
            Some(("self_knock", "an office cannot knock on itself".into()))
        } else if watch && !t.world {
            Some(("not_supported", format!("{} cannot be watched (an older Visit)", t.name)))
        } else if !watch && t.status != "available" {
            Some(("not_available", format!("{} is not available", t.name)))
        } else if !seen && t.hosting.len() >= t.max_visitors as usize {
            Some(("full", format!("{} has all the visitors it takes", t.name)))
        } else if seen && t.watched.len() >= l.max_watchers {
            Some(("full", format!("{} has all the watchers it takes", t.name)))
        } else if seen && me.watching.len() >= l.max_watching {
            Some(("too_many", format!("you watch {} plots already", l.max_watching)))
        } else if t.link.is_none() || me.link.is_none() {
            Some(("not_ready", format!("{} cannot be visited yet", if t.link.is_none() { t.name.as_str() } else { "you" })))
        } else if !watch && me.visiting.as_ref().and_then(|v| self.visits.get(v)).is_some_and(|v| v.host == tid) {
            Some(("already_visiting", format!("you are already in {}'s room", t.name)))
        } else if seen && me.watching.iter().any(|v| self.visits.get(v).is_some_and(|v| v.host == tid)) {
            Some(("already_watching", format!("you already {} {}", if link { "are linked with" } else { "watch" }, t.name)))
        } else if self.knocks.values().any(|k| k.from == oid && k.to == tid && (k.watch || k.link) == seen) {
            Some(("knock_pending", format!("your knock on {} is still waiting", t.name)))
        } else {
            None
        };
        if let Some((code, text)) = refusal {
            return self.error(oid, code, text, Some(r));
        }
        let now = Instant::now();
        let me = self.offices.get_mut(oid).expect("here");
        let (knocked, most) = if watch { (&mut me.knocked_watch, l.watch_knocks_per_minute) } else { (&mut me.knocked, l.knocks_per_minute) };
        while knocked.front().is_some_and(|t| now.duration_since(*t) > Duration::from_secs(60)) {
            knocked.pop_front();
        }
        if knocked.len() >= most {
            return self.error(oid, "rate_limited", format!("at most {most} knocks a minute"), Some(r));
        }
        knocked.push_back(now);
        let kid = format!("k{}", hex(6));
        let ttl = Duration::from_secs(l.knock_ttl);
        self.knocks.insert(kid.clone(), Knock { id: kid.clone(), from: oid.into(), to: tid.clone(), message: msg, until: now + ttl, watch, link });
        self.knock_order.push(kid.clone());
        let (me, k) = (self.me.clone(), kid.clone());
        tokio::spawn(async move {
            tokio::time::sleep(ttl).await;
            let _ = me.send(Ev::KnockExpired(k));
        });
        self.tell(&tid, &self.knock_msg(&kid));
        self.tell(oid, &FromLobby::KnockSent { id: kid, to: tid, watch, link });
    }

    fn knock_msg(&self, kid: &str) -> FromLobby {
        let k = &self.knocks[kid];
        let o = &self.offices[&k.from];
        let key = match &self.lie {
            Some(lie) => Some(lie.clone()),   // the test aid: a lobby that lies about whose knock it is
            None => o.key.clone(),
        };
        FromLobby::Knock {
            id: kid.into(), from: Knocker { id: o.id.clone(), name: o.name.clone(), sim: o.sim.clone(), key }, message: k.message.clone(),
            expires_in: k.until.saturating_duration_since(Instant::now()).as_secs_f64().round() as u64, watch: k.watch, link: k.link,
        }
    }

    fn on_answer(&mut self, oid: &str, knock: Option<String>, accept: Option<bool>, message: Option<String>) {
        let msg = message.unwrap_or_default();
        let most = self.limits3.message;
        if msg.chars().count() > most {
            return self.error(oid, "message_too_long", format!("message is over {most} characters"), Some("answer"));
        }
        let Some(kid) = knock.filter(|k| self.knocks.contains_key(k)) else {
            return self.error(oid, "not_found", "no knock with that id (answered, expired, or withdrawn)", Some("answer"));
        };
        if self.knocks[&kid].to != oid {
            return self.error(oid, "not_yours", "that knock is not on your door", Some("answer"));
        }
        let Some(accept) = accept else { return self.error(oid, "bad_message", "accept must be true or false", Some("answer")) };
        let k = self.remove_knock(&kid).expect("there");
        let knocker = k.from.clone();   // a knocker that left took its knocks with it
        if !accept {
            return self.tell(&knocker, &answered(&k, false, Some("declined"), &msg));
        }
        let seen = k.watch || k.link;
        let (me, them) = (&self.offices[oid], &self.offices[&knocker]);
        let full = if seen { me.watched.len() >= self.limits(me.proto).max_watchers } else { me.hosting.len() >= me.max_visitors as usize };
        if full {
            self.tell(&knocker, &answered(&k, false, Some("full"), &msg));
            return self.error(oid, "full", format!("you have all the {} you take", if seen { "watchers" } else { "visitors" }), Some("answer"));
        }
        // The knocker's own watches as they are now: it may have made others meanwhile (30 knocks to watch a minute).
        let lim = self.limits(them.proto).max_watching;
        if seen && (them.watching.len() >= lim || them.watching.iter().any(|v| self.visits.get(v).is_some_and(|v| v.host == oid))) {
            return self.tell(&knocker, &answered(&k, false, Some("full"), &msg));
        }
        if me.link.is_none() || them.link.is_none() {   // withdrawn meanwhile (a page that stopped hosting)
            let who = if me.link.is_none() { "you".to_string() } else { them.name.clone() };
            self.tell(&knocker, &answered(&k, false, Some("not_ready"), &msg));
            return self.error(oid, "not_ready", format!("{who} cannot be visited right now"), Some("answer"));
        }
        self.tell(&knocker, &answered(&k, true, None, &msg));
        self.start_visit(oid, &knocker, k.watch, k.link);
    }

    /// A visit agreed: the visitor leaves any room it is in, then both get `visit` with the other's link. A watch (a
    /// plot seen from outside) leaves nothing, and counts apart; so does a link (a watch let in by its door).
    fn start_visit(&mut self, host: &str, visitor: &str, watch: bool, link: bool) {
        let watch = watch || link;
        if !watch {
            if let Some(v) = self.offices[visitor].visiting.clone() {
                self.end_visit(&v, "moved");
            }
        }
        let vid = format!("v{}", hex(6));
        self.visits.insert(vid.clone(), Visit { id: vid.clone(), host: host.into(), visitor: visitor.into(), ticket: hex(16), watch, link });
        self.visit_order.push(vid.clone());
        if watch {
            self.offices.get_mut(host).expect("host").watched.insert(vid.clone());
            self.offices.get_mut(visitor).expect("visitor").watching.insert(vid.clone());
        } else {
            self.offices.get_mut(host).expect("host").hosting.insert(vid.clone());
            self.offices.get_mut(visitor).expect("visitor").visiting = Some(vid.clone());
        }
        for o in [host, visitor] {
            self.tell(o, &self.visit_msg(&vid, o));
        }
        self.announce();
    }

    fn visit_msg(&self, vid: &str, me: &str) -> FromLobby {
        let v = &self.visits[vid];
        let (role, peer) = if v.host == me { ("host", &v.visitor) } else { ("visitor", &v.host) };
        let p = &self.offices[peer];
        FromLobby::Visit {
            id: v.id.clone(), role: role.into(), peer: Peer { id: p.id.clone(), name: p.name.clone(), sim: p.sim.clone() }, ticket: v.ticket.clone(),
            peer_link: p.link.clone(), watch: v.watch, link: v.link,
        }
    }

    fn end_visit(&mut self, vid: &str, reason: &str) {
        let Some(v) = self.visits.remove(vid) else { return };
        self.visit_order.retain(|x| x != vid);
        if let Some(h) = self.offices.get_mut(&v.host) {
            h.hosting.remove(vid);
            h.watched.remove(vid);
        }
        if let Some(o) = self.offices.get_mut(&v.visitor) {
            o.watching.remove(vid);
            if o.visiting.as_deref() == Some(vid) {
                o.visiting = None;
            }
        }
        for o in [&v.host, &v.visitor] {
            self.tell(o, &FromLobby::VisitEnded { id: vid.into(), reason: reason.into() });
        }
        self.announce();
    }

    fn on_end(&mut self, oid: &str, visit: Option<String>, reason: Option<String>) {
        let Some(v) = visit.and_then(|v| self.visits.get(&v)).filter(|v| v.host == oid || v.visitor == oid) else {
            return self.error(oid, "no_such_visit", "you are not in a visit with that id", Some("end"));
        };
        let reason = reason.filter(|r| !r.is_empty()).unwrap_or_else(|| if v.visitor == oid { "left".into() } else { "kicked".into() });
        if !END_REASONS.contains(&reason.as_str()) {
            return self.error(oid, "bad_message", format!("reason must be one of {}", END_REASONS.join(", ")), Some("end"));
        }
        let id = v.id.clone();
        self.end_visit(&id, &reason);
    }

    /// A new name, if it is free; a taken or invalid one leaves the office as it was.
    fn on_rename(&mut self, oid: &str, name: Option<String>) {
        let Some(name) = name.filter(|n| proto::name_ok(n)) else {
            return self.error(oid, "name_invalid", "a name is 1-24 letters, digits, spaces, _ . -, starting and ending with a letter or digit", Some("rename"));
        };
        let low = name.to_ascii_lowercase();
        if self.by_name.get(&low).is_some_and(|o| o != oid) {
            return self.error(oid, "name_taken", format!("{name} is taken"), Some("rename"));
        }
        let me = self.offices.get_mut(oid).expect("here");
        let old = std::mem::replace(&mut me.name, name.clone());
        self.by_name.remove(&old.to_ascii_lowercase());
        self.by_name.insert(low, oid.into());
        if let Some(k) = self.offices[oid].key.clone().filter(|_| self.offices[oid].proto == Proto::V4) {
            self.groups.seen(&k, &name);
        }
        self.announce();
    }

    /// A line's text, as chat, groups and whispers take it: plain, trimmed, at most `chat` characters; and
    /// `chats_per_10s` of them in 10 s at most, of every kind together. None (said why) if not.
    fn chat_text(&mut self, oid: &str, text: Option<String>, r: &str) -> Option<String> {
        let (most, per) = (self.limits3.chat, self.limits3.chats_per_10s);
        let Some(text) = text else {
            self.error(oid, "bad_message", "text is a string", Some(r));
            return None;
        };
        let text = proto::plain(&text, most);
        if text.is_empty() {
            self.error(oid, "bad_message", format!("text is 1 to {most} characters"), Some(r));
            return None;
        }
        let now = Instant::now();
        let me = self.offices.get_mut(oid).expect("here");
        while me.said.front().is_some_and(|t| now.duration_since(*t) >= Duration::from_secs(10)) {
            me.said.pop_front();
        }
        if me.said.len() >= per {
            self.error(oid, "rate_limited", format!("at most {per} lines every 10 s"), Some(r));
            return None;
        }
        me.said.push_back(now);
        Some(text)
    }

    fn sayer(&self, oid: &str) -> Sayer {
        let o = &self.offices[oid];
        Sayer { id: o.id.clone(), name: o.name.clone(), key: o.key.clone() }
    }

    /// A line for everyone in the lobby: to every office of the same version that takes them, the one who said it
    /// too, so that it shows in order. Nothing is kept.
    fn on_chat(&mut self, oid: &str, text: Option<String>) {
        let Some(text) = self.chat_text(oid, text, "chat") else { return };
        let p = self.offices[oid].proto;
        let line: Utf8Bytes = FromLobby::LobbyChat { from: self.sayer(oid), text }.text().into();
        for o in self.offices.values().filter(|o| o.chat && o.proto == p) {
            for c in self.hearers(o) {
                c.send_text(&line);
            }
        }
    }

    /// A line for one office's user: `to`, an office of the same version that takes whispers; to it alone. Checked
    /// as chat is, and counted with it. Nothing is kept.
    fn on_whisper(&mut self, oid: &str, to: Option<String>, text: Option<String>) {
        let p = self.offices[oid].proto;
        let Some(to) = to.filter(|t| t != oid && self.offices.get(t).is_some_and(|o| o.proto == p)) else {
            return self.error(oid, "not_found", "no such office in the lobby", Some("whisper"));
        };
        if !self.offices[&to].whisper {
            return self.error(oid, "not_supported", format!("{} takes no whispers (an older Visit)", self.offices[&to].name), Some("whisper"));
        }
        let Some(text) = self.chat_text(oid, text, "whisper") else { return };
        self.tell(&to, &FromLobby::Whisper { from: self.sayer(oid), text });
    }

    // ---------- the lobby's operators ----------
    fn on_admin(&mut self, oid: &str, action: Option<String>, key: Option<String>, minutes: Option<u64>, text: Option<String>) {
        if !self.offices[oid].admin {
            return self.error(oid, "not_admin", "only the lobby's operators may", Some("admin"));
        }
        match action.as_deref() {
            Some("ban") => {
                let Some(key) = key.filter(|k| proto::key_ok(k)) else { return self.error(oid, "bad_message", "key is the key to ban (64 hex)", Some("admin")) };
                if self.cfg.admins.contains(&key) {
                    return self.error(oid, "bad_message", "an operator cannot be banned", Some("admin"));
                }
                let reason = proto::plain(text.as_deref().unwrap_or(""), 200);
                let minutes = minutes.map(|m| m.min(10 * 365 * 24 * 60));   // ten years at most: longer is for good
                self.bans.add(Ban { key: key.clone(), until: minutes.map(|m| now_unix().saturating_add(m * 60)), reason: reason.clone() });
                if let Some(them) = self.by_key.get(&key).cloned() {
                    // To the banned office's own connection (never to whoever banned it: `error` answers the asker).
                    if let Some(c) = self.offices[&them].conn.clone() {
                        c.send(&FromLobby::error("banned", format!("banned from this lobby{}", if reason.is_empty() { String::new() } else { format!(": {reason}") }), None));
                        c.close(4403, "banned");
                    }
                    self.depart(&them);
                }
                warn!(key, ?minutes, "banned by an operator");
            }
            Some("unban") => {
                let Some(key) = key else { return self.error(oid, "bad_message", "key is the key to unban", Some("admin")) };
                self.bans.remove(&key);
            }
            Some("bans") => {}
            _ => return self.error(oid, "bad_message", "action is ban, unban or bans", Some("admin")),
        }
        if self.offices.contains_key(oid) {
            let bans = self.bans.list();
            self.answer(oid, &FromLobby::LobbyBans { bans });
        }
    }

    // ---------- world groups (groups.rs keeps them) ----------
    fn groups_on_welcome(&self, oid: &str) {
        let o = &self.offices[oid];
        let Some(k) = o.key.as_ref().filter(|_| o.proto == Proto::V4) else { return };
        for g in self.groups.of(k) {
            self.tell(oid, &self.groups.view(&g, k));
        }
    }

    /// The offices online in version 4 with this key.
    fn online(&self, key: &str) -> Option<&str> {
        self.by_key.get(key).map(String::as_str).filter(|id| self.offices.get(*id).is_some_and(|o| o.proto == Proto::V4))
    }

    /// Every member of `group` online hears it as it is now (its ops with its requests).
    fn group_changed(&self, group: &str) {
        for m in self.groups.member_keys(group) {
            if let Some(id) = self.online(&m) {
                self.tell(id, &self.groups.view(group, &m));
            }
        }
    }

    /// A world's message. One with a password (making a world with one, giving it one, joining one that takes one)
    /// goes through twice: first its password is hashed or checked off this task (`later`), then it comes back as
    /// `Ev::Password` with `pw`, and is done (everything checked again: the lobby may have changed meanwhile).
    fn on_group(&mut self, oid: &str, m: ToLobby, pw: Option<Pw>) {
        let Some(me) = self.offices[oid].key.clone() else { return };
        let name = self.offices[oid].name.clone();
        let admin = self.offices[oid].admin;
        let lim = self.limits4.clone();
        let r = match &m {
            ToLobby::GroupCreate { .. } => "group_create",
            ToLobby::GroupJoin { .. } => "group_join",
            ToLobby::GroupLeave { .. } => "group_leave",
            ToLobby::GroupRemove { .. } => "group_remove",
            ToLobby::GroupUnban { .. } => "group_unban",
            ToLobby::GroupSet { .. } => "group_set",
            ToLobby::GroupOwner { .. } => "group_owner",
            ToLobby::GroupDelete { .. } => "group_delete",
            ToLobby::GroupSay { .. } => "group_say",
            _ => "groups",
        };
        let res = match m {
            ToLobby::Groups => {
                let list = self.groups.listed(|k| self.online(k).is_some());
                return self.answer(oid, &FromLobby::Groups { groups: list });
            }
            ToLobby::GroupSay { group, text } => {
                let Some(g) = group.and_then(|g| self.groups.find(&g)).filter(|g| self.groups.is_member(g, &me)) else {
                    return self.error(oid, "not_found", "you are in no world of that name", Some(r));
                };
                let Some(text) = self.chat_text(oid, text, r) else { return };
                let line: Utf8Bytes = FromLobby::GroupChat { group: g.clone(), from: self.sayer(oid), text }.text().into();
                for k in self.groups.member_keys(&g) {
                    if let Some(o) = self.online(&k).map(|id| &self.offices[id]) {
                        for c in self.hearers(o) {
                            c.send_text(&line);
                        }
                    }
                }
                return;
            }
            ToLobby::GroupCreate { group, about, open, password } => {
                let (now, hour) = (Instant::now(), Duration::from_secs(3600));
                let made = &mut self.offices.get_mut(oid).expect("here").made;
                while made.front().is_some_and(|t| now.duration_since(*t) > hour) {
                    made.pop_front();
                }
                if made.len() >= GROUPS_AN_HOUR && !admin {
                    return self.error(oid, "rate_limited", format!("at most {GROUPS_AN_HOUR} new worlds an hour"), Some(r));
                }
                let hash = match (password, pw) {
                    (Some(p), None) => {
                        let ok = crate::groups::password_ok(&p, &lim).and_then(|_| self.groups.may_create(&me, &group, &lim).map(|_| ()));
                        if let Err((code, text)) = ok {
                            return self.error(oid, code, text, Some(r));
                        }
                        let m = ToLobby::GroupCreate { group, about, open: false, password: Some(String::new()) };
                        return self.later(oid, &me, m, PwJob::Hash(p), r);
                    }
                    (Some(_), Some(Pw::Hashed(Some(h)))) => Some(h),
                    (Some(_), Some(_)) => return self.error(oid, "server_error", "the password could not be kept: try again", Some(r)),
                    (None, _) if open => None,
                    (None, _) => return self.error(oid, "bad_message", "a world is public (open), or has a password (Visit 3.1 makes either)", Some(r)),
                };
                let done = self.groups.create(&me, &name, group, about, hash, &lim);
                if done.is_ok() {
                    self.offices.get_mut(oid).expect("here").made.push_back(now);
                }
                done
            }
            ToLobby::GroupJoin { group, password } => match self.groups.may_join(&me, group.clone(), &lim) {
                Err(e) => Err(e),
                Ok((_, Joining::Open)) => self.groups.join(&me, &name, group, false, &lim),
                Ok((g, Joining::Password(hash))) => {
                    let low = g.to_ascii_lowercase();
                    let lately = self.wrong.entry(low.clone()).or_default();
                    while lately.front().is_some_and(|t| t.elapsed() > Duration::from_secs(3600)) {
                        lately.pop_front();
                    }
                    if lately.is_empty() {
                        self.wrong.remove(&low);
                    }
                    match (password, pw) {
                        (None, _) => Err(("password_required", format!("{g} takes a password"))),
                        (Some(_), _) if self.wrong.get(&low).is_some_and(|w| w.len() >= WRONG_PER_WORLD_HOUR) => {
                            Err(("rate_limited", format!("{g} has had too many wrong passwords lately: try again in a while")))
                        }
                        (Some(p), None) => {
                            let m = ToLobby::GroupJoin { group, password: Some(String::new()) };
                            return self.later(oid, &me, m, PwJob::Check(p, hash), r);
                        }
                        // Checked against the hash it has now: one changed meanwhile, a right answer to it is not.
                        (Some(_), Some(Pw::Right(right, was))) if was == hash => {
                            if !right {
                                self.wrong.entry(low).or_default().push_back(Instant::now());
                            }
                            self.groups.join(&me, &name, group, right, &lim)
                        }
                        (Some(_), Some(Pw::Right(..))) => Err(("wrong_password", format!("{g}'s password changed meanwhile: try again"))),
                        (Some(_), Some(_)) => return,
                    }
                }
            },
            ToLobby::GroupLeave { group } => self.groups.leave(&me, group),
            ToLobby::GroupRemove { group, key, ban } => self.groups.remove(&me, admin, group, key, ban),
            ToLobby::GroupUnban { group, key } => self.groups.unban(&me, admin, group, key),
            ToLobby::GroupSet { group, about, open, password } => match (password, pw) {
                (Some(p), None) => {
                    let ok = crate::groups::password_ok(&p, &lim).and_then(|_| self.groups.may_set(&me, admin, group.clone()).map(|_| ()));
                    if let Err((code, text)) = ok {
                        return self.error(oid, code, text, Some(r));
                    }
                    let m = ToLobby::GroupSet { group, about, open, password: Some(String::new()) };
                    return self.later(oid, &me, m, PwJob::Hash(p), r);
                }
                (Some(_), Some(Pw::Hashed(Some(h)))) => self.groups.set(&me, admin, group, about, open, Some(h), &lim),
                (Some(_), Some(_)) => return self.error(oid, "server_error", "the password could not be kept: try again", Some(r)),
                (None, _) => self.groups.set(&me, admin, group, about, open, None, &lim),
            },
            ToLobby::GroupOwner { group, key } => self.groups.owner(&me, admin, group, key),
            ToLobby::GroupDelete { group } => self.groups.delete(&me, admin, group),
            _ => return,
        };
        match res {
            Err((code, text)) => self.error(oid, code, text, Some(r)),
            Ok(done) => self.group_done(done),
        }
    }

    /// A password hashed or checked on a thread for slow work, then its message again (`Ev::Password`), its refusals
    /// to whoever asked. A few a minute for one office: each is a fraction of a second of a processor's time.
    fn later(&mut self, oid: &str, key: &str, m: ToLobby, job: PwJob, r: &str) {
        if self.hashing >= HASHING_AT_ONCE {
            return self.error(oid, "busy", "the lobby is checking other passwords: try again in a moment", Some(r));
        }
        if !within(&mut self.offices.get_mut(oid).expect("here").passwords, PASSWORDS_A_MINUTE) {
            return self.error(oid, "rate_limited", format!("at most {PASSWORDS_A_MINUTE} passwords a minute"), Some(r));
        }
        self.hashing += 1;
        let (tx, oid, key, replying) = (self.me.clone(), oid.to_string(), key.to_string(), self.replying.clone());
        tokio::task::spawn_blocking(move || {
            let pw = match job {
                PwJob::Hash(p) => Pw::Hashed(crate::groups::hash_password(&p)),
                PwJob::Check(p, hash) => Pw::Right(crate::groups::password_is(&p, &hash), hash),
            };
            let _ = tx.send(Ev::Password { oid, key, replying, m, pw });
        });
    }

    /// What a change to a group means for whom: everyone in it hears it whole; whoever left it, why; an op, a request.
    fn group_done(&self, done: crate::groups::Done) {
        for (key, msg) in &done.tell {
            if let Some(id) = self.online(key) {
                self.tell(id, msg);
            }
        }
        if let Some(g) = &done.changed {
            self.group_changed(g);
        }
    }
}

/// What a browser speaking for an office hears of what the office does (3.1): the lobby and its talk, the office's
/// worlds, its knocks and their answers. Not its visits' tickets and links: those are its visit-client's alone.
fn for_web(m: &FromLobby) -> bool {
    matches!(m, FromLobby::Lobby { .. } | FromLobby::LobbyChat { .. } | FromLobby::Whisper { .. } | FromLobby::Group { .. }
        | FromLobby::GroupLeft { .. } | FromLobby::GroupChat { .. }
        | FromLobby::Knock { .. } | FromLobby::KnockSent { .. }
        | FromLobby::KnockCancelled { .. } | FromLobby::Answered { .. } | FromLobby::VisitEnded { .. })
}

fn is_type(t: &str) -> bool {
    const TYPES: [&str; 30] = ["hello", "prove", "link", "status", "max_visitors", "knock", "answer", "end", "rename", "chat", "whisper", "bye",
        "about", "door", "group_create", "group_join", "group_leave", "group_remove", "group_unban", "group_set", "group_owner", "group_delete",
        "group_say", "groups", "admin", "browser_code", "browser_allow", "browsers", "browser_revoke", "browser_forget"];
    TYPES.contains(&t)
}

/// A knock's answer, for its knocker: a watch's says so, and a link's.
fn answered(k: &Knock, accept: bool, reason: Option<&str>, msg: &str) -> FromLobby {
    FromLobby::Answered { knock: k.id.clone(), accept, message: msg.into(), reason: reason.map(str::to_string), watch: k.watch, link: k.link }
}

/// A handle to a running core.
#[derive(Clone)]
pub struct Handle(pub mpsc::UnboundedSender<Ev>);

impl Handle {
    pub async fn ask<T>(&self, f: impl FnOnce(oneshot::Sender<T>) -> Ev) -> Option<T> {
        let (tx, rx) = oneshot::channel();
        self.0.send(f(tx)).ok()?;
        rx.await.ok()
    }
}

