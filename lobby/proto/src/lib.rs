//! What the lobby and a visit-client agree on (../../../visit/SPEC.md 6, ../../../LOBBY.md): the messages a
//! visit-client sends (`ToLobby`) and the lobby sends (`FromLobby`), as types; the limits; the name rule; how a
//! line of chat is made plain; and what a version-4 hello signs. Both `lobby` and `visit` are built with this
//! crate, so a field cannot be spelled two ways.
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

/// Visit 0.5 to 2.x: a lobby of its own, behind the lobby's `--v3`.
pub const PROTO3: &str = "basedverse-lobby/3";
/// Visit 3.0 on: the key proved at hello, world groups, the directory.
pub const PROTO4: &str = "basedverse-lobby/4";

/// The limits, sent in `welcome` so a client can keep to them before it sends.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Limits {
    /// Characters in a name (`name_ok`).
    pub name: usize,
    /// Characters in a knock's or an answer's message.
    pub message: usize,
    /// Bytes in one WebSocket message; a bigger one closes the connection.
    pub frame: usize,
    pub knocks_per_minute: usize,
    /// Seconds a knock waits for its answer.
    pub knock_ttl: u64,
    /// The most visitors an office may take.
    pub max_visitors: u8,
    /// The most offices watching one office's plot at once.
    pub max_watchers: usize,
    /// The most plots one office watches at once.
    pub max_watching: usize,
    /// Knocks to watch a minute (visit-clients make them by themselves).
    pub watch_knocks_per_minute: usize,
    /// Bytes of a link's `addr`, as JSON.
    pub link_addr: usize,
    pub connections_per_ip: usize,
    /// Seconds a dropped session is kept.
    pub resume: u64,
    /// Characters in a line of chat (the lobby's, a group's, a whisper).
    pub chat: usize,
    /// Lines an office may say in 10 s, of every kind together.
    pub chats_per_10s: usize,
    /// Version 4: the groups a key may be in (its own among them).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub groups: Option<usize>,
    /// Version 4: the members a group may have.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub group_members: Option<usize>,
    /// Version 4: the requests to join a group that may wait at once.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub group_pending: Option<usize>,
    /// Version 4: characters in a room's or a group's `about`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub about: Option<usize>,
    /// Version 4: a room's tags, and characters in each.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub tags: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub tag: Option<usize>,
}

impl Limits {
    /// The limits of a version-3 lobby (as the Python lobby had them), knocks waiting `knock_ttl` seconds.
    pub fn v3(knock_ttl: u64) -> Limits {
        Limits {
            name: 24, message: 280, frame: 64 * 1024, knocks_per_minute: 6, knock_ttl, max_visitors: 8, max_watchers: 8,
            max_watching: 8, watch_knocks_per_minute: 30, link_addr: 2048, connections_per_ip: 8, resume: 30, chat: 200,
            chats_per_10s: 5, groups: None, group_members: None, group_pending: None, about: None, tags: None, tag: None,
        }
    }

    /// Version 4's: version 3's, and world groups' and the directory's.
    pub fn v4(knock_ttl: u64) -> Limits {
        Limits { groups: Some(20), group_members: Some(64), group_pending: Some(50), about: Some(200), tags: Some(5), tag: Some(20), ..Limits::v3(knock_ttl) }
    }
}

/// A name: an office's, or a group's. 1-24 letters, digits, space, `_ . -`, starting and ending with a letter or
/// digit. Two names are the same name whatever their case.
pub fn name_ok(n: &str) -> bool {
    let b = n.as_bytes();
    !b.is_empty()
        && b.len() <= 24
        && b.iter().all(|c| c.is_ascii_alphanumeric() || b" _.-".contains(c))
        && b[0].is_ascii_alphanumeric()
        && b[b.len() - 1].is_ascii_alphanumeric()
}

/// A tag in a room's `about`: 1-20 lower-case letters, digits and `-`.
pub fn tag_ok(t: &str) -> bool {
    (1..=20).contains(&t.len()) && t.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

/// A line as chat takes it: control characters become spaces; trimmed; at most `n` characters (and trimmed again).
pub fn plain(text: &str, n: usize) -> String {
    let t: String = text.chars().map(|c| if c.is_control() { ' ' } else { c }).collect();
    t.trim().chars().take(n).collect::<String>().trim().to_string()
}

/// A key as the lobby takes it: an iroh endpoint id, 64 lower-case hex characters.
pub fn key_ok(k: &str) -> bool {
    k.len() == 64 && k.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// The lobby a client dialled, as both sides name it in a hello's proof: the URL's host and port, in lower case,
/// without a port of 80 or 443 (`wss://Lobby.Example:443/` is `lobby.example`). A Host header goes through the
/// same.
pub fn authority(url_or_host: &str) -> String {
    let s = url_or_host.split_once("://").map_or(url_or_host, |(_, rest)| rest);
    let s = s.split(['/', '?', '#']).next().unwrap_or("");
    let s = s.rsplit_once('@').map_or(s, |(_, h)| h).to_ascii_lowercase();
    for default in [":443", ":80"] {
        if let Some(h) = s.strip_suffix(default) {
            return h.to_string();
        }
    }
    s
}

/// What a version-4 hello's `prove` signs, with the key's secret half: the protocol, the lobby dialled (`authority`),
/// the lobby's challenge, and the key. Naming the lobby keeps one lobby from passing another's challenge on to a
/// visit-client and coming into that other lobby as it.
pub fn proof_message(host: &str, nonce: &str, key: &str) -> String {
    format!("{PROTO4} hello\n{host}\n{nonce}\n{key}")
}

/// A flag that is true only when it is `true` (anything else, or none, is false), as the lobby has always read them.
fn truthy<'de, D: Deserializer<'de>>(d: D) -> Result<bool, D::Error> {
    Ok(Value::deserialize(d)? == Value::Bool(true))
}

/// An id the lobby looks up: a string, else none (so a number is an id nobody has, refused as such).
fn id_or_none<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    Ok(match Value::deserialize(d)? {
        Value::String(s) => Some(s),
        _ => None,
    })
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// How an office describes itself in the directory (version 4).
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Default)]
pub struct About {
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub tags: Vec<String>,
}

/// A visit-client's message to the lobby. Every field the lobby checks is optional here, so a message missing one is
/// refused for what it is (`not_found`, `bad_message`), not for its shape.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ToLobby {
    /// Join, or resume a dropped session (`resume`). Version 4 says its `key` and proves it (`prove`).
    Hello {
        proto: String,
        name: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        key: Option<String>,
        sim: Value,
        status: String,
        max_visitors: u8,
        #[serde(skip_serializing_if = "Option::is_none")]
        resume: Option<String>,
        /// Version 3's: what it takes (version 4 takes them all).
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        world: bool,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        links: bool,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        chat: bool,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        whisper: bool,
        /// Version 4: the directory's line about the room, and its door open to anyone.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        about: Option<About>,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        public: bool,
    },
    /// Version 4: the challenge signed (`proof_message`), 128 hex characters.
    Prove { signature: String },
    /// How to reach this office (its endpoint, version 4: its key; `addr` passed on untouched); both null: withdrawn.
    Link { endpoint: Option<String>, addr: Option<Value> },
    Status { status: Option<String> },
    MaxVisitors { n: Option<u8> },
    Knock {
        #[serde(default, deserialize_with = "id_or_none")]
        to: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        watch: bool,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        link: bool,
    },
    Answer {
        #[serde(default, deserialize_with = "id_or_none")]
        knock: Option<String>,
        accept: Option<bool>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
    },
    End {
        #[serde(default, deserialize_with = "id_or_none")]
        visit: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
    },
    Rename { name: Option<String> },
    Chat { text: Option<String> },
    Whisper {
        #[serde(default, deserialize_with = "id_or_none")]
        to: Option<String>,
        text: Option<String>,
    },
    Bye,
    /// Version 4: the directory.
    About { about: Option<About> },
    Door { public: Option<bool> },
    /// Version 4: world groups.
    GroupCreate {
        group: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        about: Option<String>,
        #[serde(default, skip_serializing_if = "is_false", deserialize_with = "truthy")]
        open: bool,
    },
    GroupJoin { group: Option<String> },
    GroupLeave { group: Option<String> },
    GroupAnswer { group: Option<String>, key: Option<String>, accept: Option<bool> },
    GroupRemove { group: Option<String>, key: Option<String> },
    GroupSet {
        group: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        about: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        open: Option<bool>,
    },
    GroupOp { group: Option<String>, key: Option<String>, op: Option<bool> },
    GroupDelete { group: Option<String> },
    GroupSay { group: Option<String>, text: Option<String> },
    /// The groups' directory (`groups` answers).
    Groups,
    /// Version 4, the lobby's own operators: ban or unban a key, the message of the day, the bans.
    Admin {
        action: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        key: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        minutes: Option<u64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        text: Option<String>,
    },
}

impl ToLobby {
    /// Its JSON, as sent.
    pub fn to_json(&self) -> Value {
        serde_json::to_value(self).expect("a message is JSON")
    }
}

/// The sim, as an office says it (each at most 40 characters).
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Default)]
pub struct Sim {
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub version: Option<String>,
}

/// How to reach an office: its endpoint and its `addr`, as it published them.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct LinkTo {
    pub endpoint: String,
    pub addr: Value,
}

/// An office in the lobby, as `lobby` lists it.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Office {
    pub id: String,
    pub name: String,
    pub status: String,
    pub sim: Sim,
    pub max_visitors: u8,
    pub visitors: usize,
    /// The office it visits (its host's id), or null.
    pub visiting: Option<String>,
    pub ready: bool,
    /// Its key: version 4, the one it proved; version 3, the endpoint it last published (null before it has).
    pub key: Option<String>,
    pub watchers: usize,
    pub world: bool,
    pub links: bool,
    pub whisper: bool,
    /// Version 4: its line in the directory, and its door open to anyone.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub about: Option<About>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub public: Option<bool>,
}

/// Who knocked.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Knocker {
    pub id: String,
    pub name: String,
    pub sim: Sim,
    pub key: Option<String>,
}

/// The other office of a visit.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Peer {
    pub id: String,
    pub name: String,
    pub sim: Sim,
}

/// Who said a line.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Sayer {
    pub id: String,
    pub name: String,
    pub key: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Knocking {
    pub id: String,
    pub to: String,
}

/// A group's member, as its `group` says.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Member {
    pub key: String,
    /// The name it last had in this lobby.
    pub name: String,
    /// When it joined, in seconds since 1970.
    pub since: u64,
}

/// A request to join a group, waiting for an op.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Request {
    pub key: String,
    pub name: String,
}

/// A group in the directory.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Listed {
    pub name: String,
    pub about: String,
    pub open: bool,
    pub members: usize,
    /// Members in the lobby now.
    pub online: usize,
}

/// A ban on a key, lobby-wide.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Ban {
    pub key: String,
    /// Until when, in seconds since 1970; none: for good.
    pub until: Option<u64>,
    #[serde(default)]
    pub reason: String,
}

/// The lobby's message to a visit-client.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FromLobby {
    /// Version 4: sign this (`proof_message`) and send it back as `prove`.
    Challenge { nonce: String },
    Welcome {
        proto: String,
        id: String,
        name: String,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        key: Option<String>,
        relays: Vec<String>,
        resume: String,
        resumed: bool,
        visits: Vec<String>,
        knocking: Vec<Knocking>,
        limits: Limits,
        chat: bool,
        /// Version 4: this key is one of the lobby's operators.
        #[serde(skip_serializing_if = "is_false", default)]
        admin: bool,
        /// Version 4: the lobby's name and its message of the day.
        #[serde(skip_serializing_if = "Option::is_none", default)]
        title: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        motd: Option<String>,
    },
    Lobby { offices: Vec<Office> },
    Knock {
        id: String,
        from: Knocker,
        message: String,
        expires_in: u64,
        #[serde(skip_serializing_if = "is_false", default)]
        watch: bool,
        #[serde(skip_serializing_if = "is_false", default)]
        link: bool,
    },
    KnockSent {
        id: String,
        to: String,
        #[serde(skip_serializing_if = "is_false", default)]
        watch: bool,
        #[serde(skip_serializing_if = "is_false", default)]
        link: bool,
    },
    KnockCancelled { id: String, reason: String },
    Answered {
        knock: String,
        accept: bool,
        message: String,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        reason: Option<String>,
        #[serde(skip_serializing_if = "is_false", default)]
        watch: bool,
        #[serde(skip_serializing_if = "is_false", default)]
        link: bool,
    },
    Visit {
        id: String,
        role: String,
        peer: Peer,
        ticket: String,
        peer_link: Option<LinkTo>,
        #[serde(skip_serializing_if = "is_false", default)]
        watch: bool,
        #[serde(skip_serializing_if = "is_false", default)]
        link: bool,
    },
    VisitEnded { id: String, reason: String },
    LobbyChat { from: Sayer, text: String },
    Whisper { from: Sayer, text: String },
    /// Version 4: one of this office's groups, whole: on welcome, and whenever it changes. `pending` for its ops.
    Group {
        name: String,
        about: String,
        open: bool,
        owner: String,
        ops: Vec<String>,
        members: Vec<Member>,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        pending: Option<Vec<Request>>,
    },
    /// Version 4: no longer in a group (`left`, `removed`, `deleted`).
    GroupLeft { group: String, reason: String },
    /// Version 4: a request to join a group, to its ops.
    GroupRequest { group: String, from: Request },
    /// Version 4: a request to join is waiting (`accept` absent), or was answered.
    GroupAnswered {
        group: String,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        accept: Option<bool>,
    },
    GroupChat { group: String, from: Sayer, text: String },
    Groups { groups: Vec<Listed> },
    /// Version 4, to an operator: the lobby's bans (not a room's: Visit's own `bans` are those).
    LobbyBans { bans: Vec<Ban> },
    Error {
        code: String,
        message: String,
        #[serde(rename = "ref", skip_serializing_if = "Option::is_none", default)]
        reference: Option<String>,
    },
}

impl FromLobby {
    pub fn error(code: &str, message: impl Into<String>, reference: Option<&str>) -> FromLobby {
        FromLobby::Error { code: code.into(), message: message.into(), reference: reference.map(str::to_string) }
    }

    /// Its JSON text, as sent.
    pub fn text(&self) -> String {
        serde_json::to_string(self).expect("a message is JSON")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names() {
        for ok in ["A", "Magic Room", "a.b-c_d", "x1", "abcdefghijklmnopqrstuvwx"] {
            assert!(name_ok(ok), "{ok}");
        }
        for bad in ["", " a", "a ", "-a", "abcdefghijklmnopqrstuvwxy", "é", "a/b"] {
            assert!(!name_ok(bad), "{bad}");
        }
    }

    #[test]
    fn authorities() {
        assert_eq!(authority("wss://Lobby.Example:443/"), "lobby.example");
        assert_eq!(authority("ws://127.0.0.1:8765"), "127.0.0.1:8765");
        assert_eq!(authority("wss://1.2.3.4/"), "1.2.3.4");
        assert_eq!(authority("1.2.3.4:80"), "1.2.3.4");
        assert_eq!(authority("[::1]:9000"), "[::1]:9000");
        assert_eq!(authority("ws://user@host:8000/x?y"), "host:8000");
    }

    #[test]
    fn plain_lines() {
        assert_eq!(plain("  hi\nthere\u{7}  ", 200), "hi there");
        assert_eq!(plain("abcdef", 3), "abc");
        assert_eq!(plain("ab  c", 3), "ab");
        assert_eq!(plain("\n\t", 200), "");
    }

    #[test]
    fn messages_round_trip() {
        let k = ToLobby::Knock { to: Some("o1".into()), message: None, watch: true, link: false };
        assert_eq!(k.to_json(), serde_json::json!({ "type": "knock", "to": "o1", "watch": true }));
        let m: ToLobby = serde_json::from_value(serde_json::json!({ "type": "knock", "to": "o1", "watch": "yes" })).unwrap();
        assert_eq!(m, ToLobby::Knock { to: Some("o1".into()), message: None, watch: false, link: false });
        let m: ToLobby = serde_json::from_value(serde_json::json!({ "type": "max_visitors" })).unwrap();
        assert_eq!(m, ToLobby::MaxVisitors { n: None });
        let e = FromLobby::error("not_found", "no", Some("knock"));
        assert_eq!(serde_json::to_value(&e).unwrap(), serde_json::json!({ "type": "error", "code": "not_found", "message": "no", "ref": "knock" }));
    }
}
