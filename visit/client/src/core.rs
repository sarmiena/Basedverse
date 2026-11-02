//! The visit-client's core: one task owns all its state (the office, the lobby as last seen, the knocks
//! on its door, the visits) and routes what comes in from the page, the lobby and the links (../SPEC.md
//! sections 4-8). Everything else talks to it through `Ev`s.
use std::{
    collections::{BTreeMap, HashMap, HashSet, VecDeque},
    path::PathBuf,
    time::{Duration, Instant},
};

use bytes::Bytes;
use lobby_proto::{About, ToLobby};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tracing::{debug, info, trace, warn};

use crate::{
    db::{self, DbCmd, Held, Loaded, MediaTo, CHAT_KEPT, UNSEEN_KEPT},
    frames::{self, Frame},
    land::{self, Kept, Lands},
    link::{self, LinkCmd, Links, Relays, Role},
    lobby::LobbyOut,
    media,
    state::{self, Pick, Place, Saved},
    VERSION,
};

/// A page connection's role (../../engine/SPEC.md 3): the room (host), the person (user), or both at once (every page
/// of Visit 0.22 and before: a room not yet split, in the owner's tab).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PageRole {
    Host,
    User,
    Both,
}

/// The owner's visit (../../engine/SPEC.md 4): two visits of this office's own, with no link between them but a pair
/// of tasks (link::local_pair): the host's side, a watch of its plot by its owner; the user's side, that watch.
const HOME_HOST: &str = "home:host";
const HOME_USER: &str = "home:user";
/// Its one id to both pages (as a visit's is one to both sides): the host marks the owner's body with it, and the
/// owner's page knows that body as its own by it (8.1).
const HOME_ID: &str = "home";

/// What the core hears about.
pub enum Ev {
    PageOpen { id: u64, role: PageRole, text: mpsc::UnboundedSender<PageOut>, frames: mpsc::Sender<Bytes> },
    PageMsg { id: u64, msg: Value },
    PageBin { id: u64, data: Bytes },
    PageClosed { id: u64 },
    Lobby(Value),
    LobbyDown,
    /// Someone signed in on this workroom (a live session of its sign-in), or nobody any more: in the lobby only while
    /// someone is (../../LOBBY.md 2, rule 3).
    SignedIn(bool),
    /// The lobby refused this office for good (bad name, protocol): exit with this code.
    Fatal(i32),
    LinkAddr { endpoint: String, addr: Value },
    LinkUp { visit: String },
    LinkBack { visit: String },
    LinkLost { visit: String },
    LinkCtl { visit: String, msg: Value },
    LinkInput { visit: String, msg: Value },
    LinkFrame { visit: String, frame: Frame },
    LinkFailed { visit: String },
    /// The link's path (direct or through a relay) and round-trip time, about once a second.
    LinkStats { visit: String, relay: bool, rtt_ms: f64 },
    WantKey { visit: String },
    /// An edit of the scene of the room this office visits: unpacked, checked JSON (../SPEC.md section 8.3).
    LinkScene { visit: String, json: String },
    /// An edit of that scene was dropped (too big, malformed, too many): the page asks for the whole scene.
    SceneLost { visit: String },
    /// This office's scene edits for a visitor were dropped (its link far behind): the page sends it all again.
    SceneResync { visit: String },
    /// A visitor's avatar (8.6): unpacked, checked JSON, for the host's page.
    LinkAvatar { visit: String, json: String },
    /// One of a visitor's possessions' models (8.11): its id, and unpacked, checked JSON, for the host's page.
    LinkPossession { visit: String, id: String, json: String },
    /// A host's landscape this office asked for, gzipped (../SPEC.md 8.10).
    LinkLand { visit: String, gz: Bytes },
    /// A packet of the other side's voice (8.7).
    LinkAudio { visit: String, data: Bytes },
    /// Someone's voice the other side passed on (8.7, 8.8).
    LinkRelay { visit: String, data: Bytes },
    /// A voice in a room voice (8.7): a member's to this office's room (`up`), or the room's to this member.
    LinkVoice { visit: String, up: bool, data: Bytes },
    /// A picture the other side sent, as asked (8.12): checked (its bytes are its id's), its type.
    LinkMedia { visit: String, id: String, mime: String, bytes: Bytes },
    /// The database's answers (8.12): a picture asked for (none: not here); whether it has one a line named; and
    /// what the page asked for (its history, what was shared), to the page as it is.
    DbMedia { id: String, to: MediaTo, found: Option<(String, Bytes)> },
    DbHas { id: String, visit: String, has: bool },
    DbReply(Value),
    /// A link's preview, made for the page (none: nothing to show).
    Preview { req: Value, url: String, found: Option<media::Preview> },
    Tick,
    Shutdown,
}

/// What the page is told (frames go apart, on a queue that drops them when the page falls behind).
pub enum PageOut {
    Text(String),
    /// A binary message never dropped (a picture the page asked for), unlike a frame.
    Binary(Bytes),
    Close(u16, &'static str),
}

#[derive(Clone)]
struct Page {
    id: u64,
    text: mpsc::UnboundedSender<PageOut>,
    frames: mpsc::Sender<Bytes>,
}

struct Visit {
    id: String,
    channel: u16,
    role: Role,
    peer: Value,
    /// One side of the owner's visit (HOME_HOST, HOME_USER): no link, this office's own pages at both ends. Kept out
    /// of voice, chat fan-out, and every count (../../engine/SPEC.md 4).
    local: bool,
    /// Its link has passed the handshake (it has been `visit_started` to the page).
    up: bool,
    cmd: mpsc::Sender<LinkCmd>,
    /// The highest input sequence number seen, and the inputs taken in the current second (host side).
    last_seq: u64,
    inputs: (Instant, u32),
    /// A delta for the page was dropped (visitor side): ask the host for a key delta, once.
    want_key: bool,
    /// The host page's state for this visitor (where it stands), kept for a page that reloads.
    host_state: Option<Value>,
    /// Control messages taken from the other side in the current second: at most CTL_PER_SECOND.
    ctl: (Instant, u32),
    /// When a key delta the visitor asked for was last passed to the page (host side): at most every
    /// KEY_EVERY.
    key_asked: Option<Instant>,
    /// When the visitor last asked for the whole scene (host side): passed on at most every RESYNC_EVERY; one
    /// asked for sooner waits (`resync_due`) and goes when it may, on a tick (never dropped: a visitor's page
    /// that comes back has no scene until it gets one).
    resync_asked: Option<Instant>,
    resync_due: bool,
    /// Points taken from the visitor in the current second (host side): at most POINTS_PER_SECOND.
    points: (Instant, u32),
    /// A guest (3.3: someone in one of this office's worlds) let in without knocking, through the open door (host side).
    guest: bool,
    /// A watch (../WORLD.md): the visitor sees the host's plot from outside it, until it steps in (`inside`).
    watch: bool,
    /// The visitor is in the host's room: its input and points count, and the host has it. Always, for a visit;
    /// for a watch, from `enter` to `exit`.
    inside: bool,
    /// The visitor's page reads an added object that names the geometries and materials it already has by uuid
    /// only (8.3; Visit 0.7 on): its visit-client says so when the link is up (host side).
    shared_adds: bool,
    /// A link (8.5): a watch begun by a knock its host let in, not by the world. The door and the guest list do
    /// not end it, nor does the world going off: it ends as a visit does.
    link: bool,
    /// Where the page put a link (visitor side): in its world, kept for a page that reloads.
    in_world: bool,
    /// The host asked for this office's avatar (visitor side): it goes now, and again when the page gives a new one.
    avatar_asked: bool,
    /// The possessions whose models the host asked for (visitor side, 8.11): each goes now, and again when the page
    /// gives a new one. The visitor's possessions, as it last said (host side): for a page that reloads.
    possessions_asked: Vec<String>,
    peer_possessions: Value,
    /// The other office's room has a phone (8.8): it takes calls.
    peer_phone: bool,
    /// A call is on over this link (answered, one way or the other, and not hung up), and this office has it on
    /// its phone's speaker (8.8; else to the ear).
    call_on: bool,
    speaker: bool,
    /// 3.4.3: a call ringing over this link, not yet answered: rung by this office (Some(true): only the other side
    /// may answer it) or by the other (Some(false): only this office's page may). An answer for no such ring is
    /// nothing: no call is on.
    ring: Option<bool>,
    /// 4.0: a voice visit (voice alone: no scene, no room; a call from the start, unless it joins a huddle), and the
    /// world whose huddle it joins (a huddle's: in its starter's room's voice instead).
    voice: bool,
    huddle: Option<String>,
    /// 4.0 (#59): the acts its host page set on this visitor in the current second (host side: at most PERFORMS); what
    /// the host has this office's user do and hold (visitor side, `{act, hold}`: for a page that reloads).
    performs: (Instant, u32),
    performed: Value,
    /// 3.4.3: pictures the other side asked this office for in the current minute (MEDIA_WANTS at most).
    wants: (Instant, u32),
    /// Lines said by the visitor in the current window (host side): at most SAYS a SAY_WINDOW.
    says: (Instant, u32),
    /// Landscapes (8.10). Host side: the visitor reads them (else its page gets the whole room inline), and the
    /// one last sent it. Visitor side: the host's, by its hash.
    landscapes: bool,
    land_sent: Option<String>,
    land_hash: Option<String>,
    /// Who the host says is near this office's user in its room (visitor side, 8.7), by key: they are heard in
    /// person, and a call or a room voice with them goes quiet.
    near_keys: Vec<String>,
    /// The name the other office's user goes by (8.9: `user`, as their page said), if they gave one.
    peer_user: Option<String>,
}

/// Where everyone in this office's room is (8.7), in metres, as its page last said: its own user (none while they
/// are elsewhere, or the page has not said), its phone, and each visitor in it, by channel. Who hears whom in the
/// room goes by it: the room moves its visitors, so it is the room's word, not theirs.
#[derive(Default)]
struct VoiceRoom {
    me: Option<[f64; 3]>,
    phone: Option<[f64; 3]>,
    visitors: HashMap<u16, [f64; 3]>,
    /// Who each was last told is near it (by channel; 0, this office's page), and when.
    told: HashMap<u16, (Value, Instant)>,
    /// Across a plot's side (8.7): where this office's world has each friend whose room is a plot in it, by key (its
    /// owner, at home there); those it has told it has them within reach (both at home), and those who say the same
    /// of it. Both: they hear each other.
    world: HashMap<String, [f64; 3]>,
    world_told: HashSet<String>,
    world_heard: HashSet<String>,
}

/// The most control messages a link's other side may send a second; more are dropped (a peer flooding
/// requests must not swamp this office's page).
const CTL_PER_SECOND: u32 = 20;
const KEY_EVERY: Duration = Duration::from_millis(500);
const RESYNC_EVERY: Duration = Duration::from_secs(2);
/// The most inputs a second taken from a visitor (it sends one whenever its keys or look change, at most
/// every 8 ms, and every 33 ms regardless).
const INPUTS_PER_SECOND: u32 = 120;
/// Voice (8.7): heard within VOICE_RANGE metres of whoever speaks, in the same room, fading to nothing there; sent
/// VOICE_MARGIN farther, so a voice fades in rather than starts. Who is near whom: told when that changes, and
/// their distances at most every NEAR_EVERY.
const VOICE_RANGE: f64 = 45.0;
const VOICE_MARGIN: f64 = 15.0;
const NEAR_EVERY: Duration = Duration::from_secs(1);
/// 4.0 (#59): what a room may have a visitor's own model do (MODELS.md, "Clips"), and how often it may change it.
const ACTS: [&str; 7] = ["sit", "lie", "drink", "eat", "throw", "swat", "wave"];
const PERFORMS: u32 = 10;
/// 4.0: what a voice visit carries on its control stream (link_ctl): its user's name, a ban, a room's voice.
const VOICE_CTL: [&str; 5] = ["user", "banned", "voice_join", "voice_members", "voice_out"];
/// 4.0: how long a knock to talk this office made is waited for (the lobby's knocks wait 60 s).
const VOICE_ASK: Duration = Duration::from_secs(90);
/// Chat (8.9): a line said is at most SAY_LEN characters; each office says at most SAYS lines a SAY_WINDOW (its
/// page, and each visitor in its room, apart).
const SAY_LEN: usize = 200;
const SAYS: u32 = 5;
const SAY_WINDOW: Duration = Duration::from_secs(10);
/// Whispers left for someone away (8.9): at most this many in all, and for one person; passed on one at a time, at
/// most one every HELD_EVERY.
const HELD_MAX: usize = 100;
const HELD_EACH: usize = 20;
const HELD_EVERY: Duration = Duration::from_millis(2500);
/// Pictures (8.12) kept in memory too, the last few, while the database takes them.
const MEDIA_RECENT: usize = 8;
/// 3.4.3: the most pictures a linked office may ask this one for a minute (as many as it takes from it: link.rs
/// MEDIA_PER_MINUTE); more, and they are not read nor sent.
const MEDIA_WANTS: u32 = 30;
/// The most points a second taken from a visitor (a click at the middle of its view: 8.3); more are dropped.
/// Its page sends at most one every 200 ms.
const POINTS_PER_SECOND: u32 = 5;
/// A room's one-shot sounds (3.4, #36) its page plays, at most this many a second, for everyone in it.
const SOUNDS_PER_SECOND: u32 = 10;
/// The most notches of the wheel one point carries, either way (8.3): its page adds up those between its points.
const MAX_WHEEL: f64 = 10.0;
/// How long a guest let in through the open door is waited for, to be agreed as a visit by the lobby.
const ADMITTED_FOR: Duration = Duration::from_secs(120);
/// The world (../WORLD.md): a knock to watch a picked plot not yet answered is not made again for this long;
/// one declined (or refused by the lobby), for this long.
const WATCH_PENDING: Duration = Duration::from_secs(15);
const WATCH_DECLINED: Duration = Duration::from_secs(20);
/// The most plots an office watches at once (its picks: the lobby's own limit is the same).
const MAX_WATCHING: usize = state::MAX_PICKS;
/// With no page for this long, its world's watches end (they are only for a page to draw); a new page that has
/// not asked for the world this long after connecting, the same.
const WORLD_NO_PAGE: Duration = Duration::from_secs(60);
/// After a welcome, how long the lobby has to say this office's groups again before those it does not say are gone.
const GROUPS_SETTLE: Duration = Duration::from_secs(3);
const WORLD_ASK: Duration = Duration::from_secs(10);

pub struct Core {
    /// The lobby's address (VISIT_LOBBY), for a web pass to its page (3.1).
    lobby_url: Option<String>,
    lobby: mpsc::UnboundedSender<LobbyOut>,
    me: mpsc::UnboundedSender<Ev>,
    relays: Option<Relays>,
    links: Option<Links>,
    link: Option<(String, Value)>,
    /// The page connections (../../engine/SPEC.md 3): the room's (host) and the person's (user). A page without a role is
    /// both: the same connection in both slots.
    host_page: Option<Page>,
    user_page: Option<Page>,
    page_gone: Instant,
    /// The connection whose message is being handled (its role), for what answers it (`error`, `media_ready`); and,
    /// while a page is welcomed, the one everything goes to.
    replying: Option<(u64, PageRole)>,
    only: Option<u64>,
    frame_refused_at: Option<Instant>,
    idle_exit: u64,
    online: bool,
    /// Someone is signed in on this workroom: the lobby is joined only then.
    signed_in: bool,
    /// The lobby relays chat (8.9: its welcome said so): this office's page may say a line to everyone there.
    lobby_chat: bool,
    /// What the lobby's welcome said of itself: its title, its message of the day, whether this office is one of its
    /// operators.
    lobby_info: Value,
    /// This office's world groups (../../LOBBY.md), as the lobby last told each (`group`), by name in lower case: their
    /// members may watch this office's plot through its open door, as guests may, and it theirs.
    groups: BTreeMap<String, Value>,
    /// After a welcome, the groups it had, until the lobby has had time to say them all again (`groups_settle`): until
    /// then they count too, so nobody's watch is ended for a group not yet said again; then any not said again are
    /// gone (left, or ended, while this office was away).
    groups_before: BTreeMap<String, Value>,
    groups_settle: Option<Instant>,
    /// What outlasts a run, kept in `state_file`: whether the page took this office out of the lobby
    /// (leave_lobby, until join_lobby), its key, its door and whose plots its world shows (its picks).
    saved: Saved,
    state_file: Option<PathBuf>,
    /// The office's key, as others see it: its link endpoint's public key.
    key: String,
    /// Guests let in through the open door (knocker's office id -> its key, and when), until the lobby agrees
    /// their visit: then the visitor's link must be that key.
    admitted: HashMap<String, (String, Instant)>,
    /// The page draws a world (../WORLD.md): this office watches the plots its user picked, by itself. After a page
    /// connects, until it asks for the world again (or WORLD_ASK passes, and the world goes off).
    world: bool,
    world_ask: Option<Instant>,
    /// Knocks to watch: sent (office id -> when, and in the order sent: the lobby answers each, in order, with
    /// knock_sent or an error), sent and named by the lobby (knock id -> office id), and declined or refused
    /// (key -> when): not made again for a while.
    watch_pending: HashMap<String, Instant>,
    watch_order: VecDeque<String>,
    watch_knocks: HashMap<String, String>,
    watch_declined: HashMap<String, Instant>,
    office: Value,
    status: String,
    max_visitors: u8,
    lobby_view: Option<Value>,
    /// This office's own avatar, gzipped JSON (8.6), as its page last gave it: for the hosts that ask.
    avatar: Option<Bytes>,
    /// This office's own possessions (8.11), as its page last gave them: what each is (every host it visits is
    /// told), and its model, gzipped JSON, for the hosts that ask.
    possessions: Vec<(Value, Option<Bytes>)>,
    /// This office's room has a phone (8.8), as its page said: every link is told.
    phone: bool,
    /// Where everyone in this office's room is, for its voice (8.7).
    voice: VoiceRoom,
    /// Lines this office's page said in the current window (8.9).
    says: (Instant, u32),
    /// Lines each of this room's own figures said (`say_as`, 8.9), by its id, in its current window.
    npc_says: HashMap<String, (Instant, u32)>,
    /// 3.4 (#36): this room's one-shots in the last second.
    sounds_at: (Instant, u32),
    /// The chat this office's user has had (8.9), theirs too: the last CHAT_KEPT lines, each with when, for a page
    /// that comes back (a reload); all of it is in the database (8.12), and this run began with its last lines.
    chat: VecDeque<Value>,
    /// The database (8.12): what this office keeps, asked of its task.
    db: mpsc::UnboundedSender<DbCmd>,
    /// The lines this office's user has not seen yet (8.9: the mailbox): the newest UNSEEN_KEPT, and how many there are
    /// in each conversation (all of them are marked in the database); and whether someone is looking at its page's chat
    /// now (its page says: `attention`), when a line meant for them is seen as it comes.
    unseen: VecDeque<Value>,
    unseen_by: BTreeMap<String, u64>,
    attention: bool,
    /// Whispers left for someone away (8.9), oldest first, kept in the database too: each passed on through the lobby
    /// once they and this office are both in it, one at a time (not before `held_next`).
    held: Vec<Held>,
    held_next: Instant,
    held_seq: u64,
    /// Pictures a line named that this office does not have (or had not, when asked): when, from which visit, and
    /// whether that visit was asked for it yet; and who waits for each (the page, a link).
    media_asked: HashMap<String, (Instant, String, bool)>,
    media_waiting: HashMap<String, Vec<MediaTo>>,
    /// The last pictures kept (MEDIA_RECENT): one asked for while still on its way into the database is had here.
    media_recent: VecDeque<(String, String, Bytes)>,
    /// The name this office's user goes by (8.9), as its page said (`user`): shown to others with what they say.
    user_name: Option<String>,
    /// The room voice this office's user is in (8.7), by its room's owner's key (this office's own: its own room's);
    /// and who else is in this office's own room's voice, by key (their names).
    room_voice: Option<String>,
    voice_members: BTreeMap<String, Value>,
    /// Rooms that keep this office's user out of their voice or chat (8.9), by key: (their name, voice, chat).
    banned_by: HashMap<String, (Value, bool, bool)>,
    /// This office's own landscape (8.10), as its page last gave it: its hash, and it gzipped. And friends' kept.
    land: Option<(String, Bytes)>,
    lands: Lands,
    /// Where this office's page has put others in its world (8.4), by key: each told over every link up with it.
    seen_out: HashMap<String, Value>,
    /// Where others have put this office in their worlds, by key: (their name, where), for a page that comes back;
    /// forgotten with the last link to them.
    seen_in: HashMap<String, (Value, Value)>,
    /// Knocks on this office's door still waiting, and when each came, for a page that connects later.
    knocks: BTreeMap<String, (Value, Instant)>,
    /// 4.0: the lobby carries voice knocks and huddles (its welcome said so). An older one would pass a voice knock on
    /// as a knock to visit: none is sent to it.
    lobby_voice: bool,
    /// 4.0: knocks to talk this office's page made (the office knocked on -> when), and those it (or this visit-client,
    /// for its huddle) answered yes (the knocker's office id -> its key, when). A voice visit the lobby agrees is a
    /// call at once: only one this office asked for, or let in, and with the key it let in; any other is ended.
    voice_asked: HashMap<String, Instant>,
    voice_let: HashMap<String, (String, Instant)>,
    /// 4.0: the world whose huddle this office's user started (its room's voice carries it); and the huddles going on in
    /// its worlds, as the lobby last said (by world, lower case: the page's `huddle` message), for a page that connects.
    huddle: Option<String>,
    huddles: BTreeMap<String, Value>,
    visits: HashMap<String, Visit>,
    next_channel: u16,
    /// The three.js revision of the scene this office's page hosts (`host`): only then is the office in the
    /// lobby to be visited, or knocking (../SPEC.md section 4.2). None while no page hosts.
    hosting: Option<String>,
    input_seq: u64,
}

impl Core {
    pub fn new(lobby_url: Option<String>, lobby: mpsc::UnboundedSender<LobbyOut>, me: mpsc::UnboundedSender<Ev>, relays: Option<Relays>, max_visitors: u8, idle_exit: u64,
               saved: Saved, fresh: bool, state_file: Option<PathBuf>, lands: Lands, db: mpsc::UnboundedSender<DbCmd>, loaded: Loaded) -> Core {
        let key = saved.key.public().to_string();
        let core = Core {
            lobby_url,
            lobby,
            me,
            relays,
            links: None,
            link: None,
            host_page: None,
            user_page: None,
            page_gone: Instant::now(),
            replying: None,
            only: None,
            frame_refused_at: None,
            idle_exit,
            online: false,
            signed_in: false,
            lobby_chat: false,
            lobby_info: Value::Null,
            groups: BTreeMap::new(),
            groups_before: BTreeMap::new(),
            groups_settle: None,
            saved,
            state_file,
            key,
            admitted: HashMap::new(),
            world: false,
            world_ask: None,
            watch_pending: HashMap::new(),
            watch_order: VecDeque::new(),
            watch_knocks: HashMap::new(),
            watch_declined: HashMap::new(),
            office: Value::Null,
            status: "available".into(),
            max_visitors,
            lobby_view: None,
            avatar: None,
            possessions: Vec::new(),
            phone: false,
            voice: VoiceRoom::default(),
            says: (Instant::now(), 0),
            npc_says: HashMap::new(),
            sounds_at: (Instant::now(), 0),
            chat: loaded.chat.into_iter().collect(),
            db,
            unseen: loaded.unseen.into_iter().collect(),
            unseen_by: loaded.unseen_by,
            attention: false,
            held: loaded.held,
            held_next: Instant::now(),
            held_seq: 0,
            media_asked: HashMap::new(),
            media_waiting: HashMap::new(),
            media_recent: VecDeque::new(),
            user_name: None,
            room_voice: None,
            voice_members: BTreeMap::new(),
            banned_by: HashMap::new(),
            land: None,
            lands,
            seen_out: HashMap::new(),
            seen_in: HashMap::new(),
            knocks: BTreeMap::new(),
            lobby_voice: false,
            voice_asked: HashMap::new(),
            voice_let: HashMap::new(),
            huddle: None,
            huddles: BTreeMap::new(),
            visits: HashMap::new(),
            next_channel: 1,
            hosting: None,
            input_seq: 0,
        };
        if fresh {
            core.save_state();   // a key made just now: kept, so it is this office's from now on
        }
        core
    }

    /// Runs until shut down; the exit code.
    pub async fn run(mut self, mut rx: mpsc::UnboundedReceiver<Ev>) -> i32 {
        while let Some(ev) = rx.recv().await {
            match ev {
                Ev::Shutdown => break,
                Ev::Fatal(code) => {
                    self.shutdown().await;
                    return code;
                }
                Ev::Tick => {
                    self.resyncs();
                    if self.groups_settle.is_some_and(|t| Instant::now() >= t) {
                        // The lobby has said this office's groups again: those it did not are gone (left, or ended,
                        // while it was away), and so are the watches they alone allowed.
                        self.groups_settle = None;
                        for (_, g) in std::mem::take(&mut self.groups_before) {
                            let name = g.get("name").cloned().unwrap_or(Value::Null);
                            self.to_page(json!({ "type": "group_left", "group": name, "reason": "left" }));
                        }
                        self.watchers_still_allowed();
                    }
                    self.media_expire();
                    self.admitted.retain(|_, (_, at)| at.elapsed() < ADMITTED_FOR);
                    if self.world && self.user_page.is_none() && self.page_gone.elapsed() >= WORLD_NO_PAGE {
                        info!("no page for a minute: its world's watches end");
                        self.set_world(false);
                    }
                    if self.world && self.user_page.is_some() && self.world_ask.is_some_and(|t| t.elapsed() >= WORLD_ASK) {
                        info!("the page does not draw a world: its watches end");
                        self.set_world(false);
                    }
                    self.watch_picks();
                    self.deliver_held();
                    if self.idle_exit > 0 && self.host_page.is_none() && self.user_page.is_none() && self.page_gone.elapsed() >= Duration::from_secs(self.idle_exit) {
                        info!("no page for {} s: exiting", self.idle_exit);
                        break;
                    }
                }
                ev => self.handle(ev).await,
            }
        }
        self.shutdown().await;
        0
    }

    async fn shutdown(&mut self) {
        let _ = self.lobby.send(LobbyOut::Bye);
        for v in self.visits.values() {
            let _ = v.cmd.try_send(LinkCmd::Close("gone".into()));
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
        if let Some(links) = &self.links {
            links.close().await;
        }
    }

    async fn handle(&mut self, ev: Ev) {
        match ev {
            Ev::PageOpen { id, role, text, frames } => self.page_open(id, role, text, frames),
            Ev::PageClosed { id } => {
                let Some(role) = self.role_of(id) else { return };
                self.page_gone = Instant::now();
                if role != PageRole::User {   // the room's page went
                    self.host_page = None;
                    self.tell_hosted(json!({ "paused": true }));
                    self.set_hosting(None);   // no page, no room to show: not to be visited until one hosts again
                    self.set_phone(false);    // nor a phone to answer: the other side's calls end
                    self.voice.me = None;     // nobody placed: nobody hears, nor is heard, in this room
                    self.voice.phone = None;
                    self.voice.visitors.clear();
                }
                if role != PageRole::Host {   // the person's page went
                    self.user_page = None;
                    self.attention = false;   // nobody looking: what comes is in the mailbox
                    self.voice.world.clear();
                    self.home_end();          // the owner's visit: nobody to be
                }
                self.tell_near();
            }
            Ev::PageMsg { id, msg } => {
                if let Some(role) = self.role_of(id) {
                    self.replying = Some((id, role));
                    let t = msg.get("type").and_then(Value::as_str).unwrap_or("");
                    if role_may(role, t) {
                        self.page_msg(msg);
                    } else {
                        self.page_error("wrong_role", &format!("{t:?} is not the {}'s to send", if role == PageRole::Host { "room" } else { "person" }), Some(t));
                    }
                    self.replying = None;
                }
            }
            Ev::PageBin { id, data } => {
                if let Some(role) = self.role_of(id) {
                    self.replying = Some((id, role));
                    if frame_may(role, &data) {
                        self.page_frame(&data);
                    } else if self.frame_refused_at.is_none_or(|t| t.elapsed() >= Duration::from_secs(1)) {   // said once a second, not once a frame
                        self.frame_refused_at = Some(Instant::now());
                        self.page_error("wrong_role", "a frame of that kind is not this connection's to send", Some("frame"));
                    }
                    self.replying = None;
                }
            }
            Ev::Lobby(msg) => self.lobby_msg(msg).await,
            Ev::LobbyDown => {
                if self.online {
                    self.online = false;
                    self.to_page(json!({ "type": "offline" }));
                }
            }
            Ev::SignedIn(s) => self.set_signed_in(s),
            Ev::LinkAddr { endpoint, addr } => {
                self.link = Some((endpoint, addr));
                self.publish_link();
            }
            Ev::LinkUp { visit } => self.link_up(&visit, true),
            Ev::LinkBack { visit } => self.link_up(&visit, false),
            Ev::LinkLost { visit } => info!(%visit, "link lost"),
            Ev::LinkCtl { visit, msg } => self.link_ctl(&visit, msg),
            Ev::LinkInput { visit, msg } => self.link_input(&visit, msg),
            Ev::LinkFrame { visit, frame } => self.link_frame(&visit, frame),
            Ev::LinkFailed { visit } => {
                warn!(%visit, "the link could not be made or came back");
                self.to_lobby(end(&visit, Some("link_failed")));
            }
            Ev::LinkStats { visit, relay, rtt_ms } => {
                if let Some(v) = self.visits.get(&visit) {
                    let ch = v.channel;
                    self.to_page(json!({ "type": "link_stats", "channel": ch, "path": if relay { "relay" } else { "direct" }, "rtt_ms": (rtt_ms * 10.0).round() / 10.0 }));
                }
            }
            Ev::WantKey { visit } => {
                if let Some(v) = self.visits.get(&visit) {
                    let ch = v.channel;
                    self.to_page(json!({ "type": "keyframe", "channel": ch }));
                }
            }
            Ev::LinkScene { visit, json } => {
                if let Some(v) = self.visits.get(&visit).filter(|v| v.role == Role::Visitor) {
                    // Already checked JSON: wrapped as it is, not parsed again.
                    let msg = format!("{{\"type\":\"scene\",\"channel\":{},\"edit\":{json}}}", v.channel);
                    if let Some(p) = &self.user_page {
                        let _ = p.text.send(PageOut::Text(msg));
                    }
                }
            }
            Ev::LinkLand { visit, gz } => self.land_in(&visit, gz),
            Ev::LinkAudio { visit, data } => self.voice_in(&visit, data, false),
            Ev::LinkRelay { visit, data } => self.voice_in(&visit, data, true),
            Ev::LinkVoice { visit, up, data } => self.room_voice_in(&visit, up, data),
            Ev::LinkMedia { visit, id, mime, bytes } => self.media_in(&visit, id, mime, bytes),
            Ev::DbMedia { id, to, found } => self.db_media(id, to, found),
            Ev::DbHas { id, visit, has } => self.db_has(id, &visit, has),
            Ev::DbReply(m) => self.to_page(m),
            Ev::Preview { req, url, found } => self.preview_in(req, url, found),
            Ev::LinkPossession { visit, id, json } => {
                if let Some(v) = self.visits.get(&visit).filter(|v| v.role == Role::Host) {
                    // Already checked JSON: wrapped as it is, not parsed again (the id was checked as it came).
                    let msg = format!("{{\"type\":\"possession\",\"channel\":{},\"id\":{},\"possession\":{json}}}", v.channel, json!(id));
                    if let Some(p) = &self.host_page {
                        let _ = p.text.send(PageOut::Text(msg));
                    }
                }
            }
            Ev::LinkAvatar { visit, json } => {
                if let Some(v) = self.visits.get(&visit).filter(|v| v.role == Role::Host) {
                    // Already checked JSON: wrapped as it is, not parsed again.
                    let msg = format!("{{\"type\":\"avatar\",\"channel\":{},\"avatar\":{json}}}", v.channel);
                    if let Some(p) = &self.host_page {
                        let _ = p.text.send(PageOut::Text(msg));
                    }
                }
            }
            Ev::SceneLost { visit } | Ev::SceneResync { visit } => {
                let lost = self.visits.get(&visit).map(|v| (v.channel, v.role));
                match lost {
                    Some((ch, Role::Visitor)) => self.to_page(json!({ "type": "scene_lost", "channel": ch })),
                    Some((ch, Role::Host)) => self.to_page(json!({ "type": "scene_resync", "channel": ch })),
                    None => {}
                }
            }
            Ev::Tick | Ev::Shutdown | Ev::Fatal(_) => {}
        }
    }

    // ---------- the page ----------

    /// Which role(s) a page connection has, by its id (both: a page without a role).
    fn role_of(&self, id: u64) -> Option<PageRole> {
        let h = self.host_page.as_ref().is_some_and(|p| p.id == id);
        let u = self.user_page.as_ref().is_some_and(|p| p.id == id);
        match (h, u) {
            (true, true) => Some(PageRole::Both),
            (true, false) => Some(PageRole::Host),
            (false, true) => Some(PageRole::User),
            (false, false) => None,
        }
    }

    /// The connections a message for this role goes to: one, when both roles are one page.
    fn pages_for(&self, to: PageRole) -> Vec<&Page> {
        let mut out: Vec<&Page> = Vec::new();
        if to != PageRole::User {
            out.extend(self.host_page.as_ref());
        }
        if to != PageRole::Host {
            if let Some(u) = &self.user_page {
                if !out.iter().any(|p| p.id == u.id) {
                    out.push(u);
                }
            }
        }
        out
    }

    fn page_by_id(&self, id: u64) -> Option<&Page> {
        [&self.host_page, &self.user_page].into_iter().flatten().find(|p| p.id == id)
    }

    /// A page slot taken: what was there is closed (4409), and, if that connection was both roles, gone from the
    /// other slot too.
    fn take_slot(&mut self, host: bool) {
        let old = if host { self.host_page.take() } else { self.user_page.take() };
        if let Some(old) = old {
            let other = if host { &mut self.user_page } else { &mut self.host_page };
            if other.as_ref().is_some_and(|p| p.id == old.id) {
                *other = None;
            }
            let _ = old.text.send(PageOut::Close(4409, "replaced"));
        }
    }

    fn page_open(&mut self, id: u64, role: PageRole, text: mpsc::UnboundedSender<PageOut>, frames: mpsc::Sender<Bytes>) {
        let page = Page { id, text, frames };
        if role == PageRole::Both {   // one page for both: no owner's visit (it moves its own user itself); the pages it replaces hear it end
            self.home_end();
        }
        if role != PageRole::User {
            self.take_slot(true);
            self.host_page = Some(page.clone());
        }
        if role != PageRole::Host {
            self.take_slot(false);
            self.user_page = Some(page);
            self.attention = false;   // until it says someone is looking
            self.world_ask = self.world.then(Instant::now);   // the world goes on only if this page asks for it too
        }
        // The owner's visit, if the room is up and the person is here now (../../engine/SPEC.md 4): the other page hears
        // of it as a visit starting; this one, in its ready.
        self.home_start(Some(id));
        self.only = Some(id);   // the welcome goes to this page alone
        let ready = self.ready_json(role);
        self.to_page(ready);
        if let Some(view) = self.lobby_view.clone() {
            self.to_page(view);
        }
        if role != PageRole::Host {
            if !self.chat.is_empty() {   // the chat this office's user had (8.9), lines that came while no page was here too
                self.to_page(json!({ "type": "chat_log", "lines": self.chat }));
            }
            if !self.unseen_by.is_empty() {   // and those of them meant for them that they have not seen (the mailbox)
                self.tell_unseen();
            }
            if !self.held.is_empty() {   // whispers they left for someone away, still here
                self.tell_held();
            }
            if let Some(k) = self.room_voice.clone().filter(|k| *k != self.key) {   // a room voice its user is in: who is in it, again
                self.to_peer(&k, json!({ "voice_join": true }));
            }
            // Knocks still waiting, each with the time it has left.
            for (k, at) in self.knocks.values().cloned().collect::<Vec<_>>() {
                let left = k.get("expires_in").and_then(Value::as_u64).unwrap_or(60).saturating_sub(at.elapsed().as_secs());
                let mut k = k;
                k["expires_in"] = json!(left);
                self.to_page(k);
            }
        }
        self.only = None;
        if role != PageRole::User {
            self.tell_hosted(json!({ "paused": false }));
        }
        if role != PageRole::Host {
            // A visit this page is on: the host's whole scene again (a page that comes back has none), and a key delta;
            // and its landscape, kept here (8.10).
            let mut lands = Vec::new();
            for v in self.visits.values().filter(|v| v.up && v.role == Role::Visitor) {
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "scene_resync": true })));
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "keyframe": true })));
                if let (Some(h), Some(key)) = (&v.land_hash, v.peer.get("key").and_then(Value::as_str)) {
                    lands.push((v.channel, key.to_string(), h.clone()));
                }
            }
            for (ch, key, h) in lands {
                if let Some(gz) = self.lands.get(&key, &h) {
                    self.land_to_page(ch, &key, gz);
                }
            }
        }
    }

    /// `ready` (5.1; ../../engine/SPEC.md 3.2) for a page of this role: the visits it is party to (the room's to a host,
    /// the person's and the room's to a user, each side of the owner's visit to its own), and the owner's visit by its
    /// name on each side.
    fn ready_json(&self, role: PageRole) -> Value {
        let visits: Vec<Value> = self.visits.values().filter(|v| v.up && visit_for(v).map_or(true, |r| r == role || role == PageRole::Both)).map(started).collect();
        let mut m = json!({
            "type": "ready", "version": VERSION, "office": self.office, "online": self.online, "lobby_chat": self.lobby_chat, "lobby_voice": self.lobby_voice, "huddles": self.huddles.values().cloned().collect::<Vec<_>>(), "away": self.saved.away, "status": self.status,
            "signed_in": self.signed_in, "lobby_info": self.lobby_info, "groups": self.groups.values().cloned().collect::<Vec<_>>(),
            "about": self.saved.about,
            "max_visitors": self.max_visitors, "visits": visits, "key": self.key, "door": self.door(), "picks": self.saved.picks_json(), "ignored": self.saved.ignored_json(),
            "layout": self.saved.layout_json(), "avatars": self.saved.avatars_json(), "voice_range": VOICE_RANGE, "kept": self.lands.kept_json(),
            "seen": self.seen_in.iter().map(|(k, (name, at))| json!({ "key": k, "name": name, "at": at })).collect::<Vec<_>>(),
            "users": self.users_json(),
            "bans": self.saved.bans_json(), "banned_by": self.banned_by_json(), "room_voice": self.room_voice_json(), "voice_members": self.voice_members_json(),
            "role": match role { PageRole::Host => json!("host"), PageRole::User => json!("user"), PageRole::Both => Value::Null },
        });
        match role {
            PageRole::User => m["home"] = self.visits.get(HOME_USER).map_or(Value::Null, |v| json!({ "id": HOME_ID, "channel": v.channel, "paused": self.host_page.is_none() })),
            PageRole::Host => m["owner"] = self.visits.get(HOME_HOST).map_or(Value::Null, |v| json!({ "id": HOME_ID, "channel": v.channel })),
            PageRole::Both => {}
        }
        m
    }

    // ---------- the owner's visit (../../engine/SPEC.md 4) ----------

    /// The owner's visit begun, when the room is up (a host page hosting), the person is here (a user page), and they
    /// are not one page: a visit of the host's with no link, and the watch that is it on the user's side. Announced to
    /// each page but `quiet` (which hears of it in its ready).
    fn home_start(&mut self, quiet: Option<u64>) {
        let (Some(h), Some(u)) = (&self.host_page, &self.user_page) else { return };
        if h.id == u.id || self.hosting.is_none() || self.visits.contains_key(HOME_HOST) {
            return;
        }
        let (host_cmd, user_cmd) = link::local_pair(self.me.clone(), HOME_HOST.to_string(), HOME_USER.to_string());
        let peer = json!({ "id": self.office.get("id"), "name": self.office.get("name"), "key": self.key, "sim": {} });
        for (id, role, cmd) in [(HOME_HOST, Role::Host, host_cmd), (HOME_USER, Role::Visitor, user_cmd)] {
            let channel = self.channel();
            self.visits.insert(id.to_string(), Visit {
                id: id.to_string(), channel, role, peer: peer.clone(), local: true, up: false, cmd,
                last_seq: 0, inputs: (Instant::now(), 0), want_key: false, host_state: None, ctl: (Instant::now(), 0), key_asked: None, resync_asked: None, resync_due: false,
                points: (Instant::now(), 0), guest: false, watch: true, inside: false, shared_adds: false, link: false, in_world: false, avatar_asked: false, possessions_asked: Vec::new(), peer_possessions: json!([]), peer_phone: false,
                call_on: false, speaker: false, ring: None, voice: false, huddle: None, performs: (Instant::now(), 0), performed: Value::Null, wants: (Instant::now(), 0), says: (Instant::now(), 0),
                landscapes: false, land_sent: None, land_hash: None, near_keys: Vec::new(), peer_user: None,
            });
        }
        info!("the owner's visit: the room has its owner");
        let (hid, uid) = (self.host_page.as_ref().map(|p| p.id), self.user_page.as_ref().map(|p| p.id));
        self.link_up_quiet(HOME_HOST, quiet.is_some() && quiet == hid);
        self.link_up_quiet(HOME_USER, quiet.is_some() && quiet == uid);
        self.home_enter();
    }

    /// The person into their own room (its host spawns them), unless they are in another: when the owner's visit begins,
    /// and when a visit elsewhere ends.
    fn home_enter(&mut self) {
        if self.inside_visit().is_some() {
            return;
        }
        if let Some(v) = self.visits.get_mut(HOME_USER).filter(|v| v.up && !v.inside) {
            v.inside = true;
            let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "enter": null })));
        }
    }

    /// The owner's visit over (the person's page gone): both sides ended, each page told.
    fn home_end(&mut self) {
        for id in [HOME_USER, HOME_HOST] {
            if self.visits.contains_key(id) {
                self.end_visit(id, "gone");
            }
        }
    }

    /// The role of the connection whose message is being handled (both, when none is).
    fn replying_role(&self) -> PageRole {
        self.replying.map_or(PageRole::Both, |(_, r)| r)
    }

    fn page_msg(&mut self, m: Value) {
        let t = m.get("type").and_then(Value::as_str).unwrap_or("").to_string();
        match t.as_str() {
            "" => self.page_error("bad_message", "a message is a JSON object with a string `type`", None),
            "hello" => self.page_error("already_hello", "this connection has said hello", Some("hello")),
            "status" => match m.get("status").and_then(Value::as_str) {
                Some(s @ ("available" | "busy")) => {
                    self.status = s.to_string();
                    self.to_lobby(ToLobby::Status { status: Some(s.to_string()) });
                }
                _ => self.page_error("bad_message", "status must be available or busy", Some("status")),
            },
            "max_visitors" => match m.get("n").and_then(Value::as_u64).filter(|n| *n <= 8) {
                Some(n) => {
                    self.max_visitors = n as u8;
                    self.to_lobby(ToLobby::MaxVisitors { n: Some(n as u8) });
                }
                None => self.page_error("bad_message", "n must be 0 to 8", Some("max_visitors")),
            },
            "leave_lobby" => self.set_away(true),
            // The door: open (its worlds' members walk in and watch its plot), or shut (kept in the state file).
            "door" => match m.get("open").and_then(Value::as_bool) {
                None => self.page_error("bad_message", "open must be true or false", Some("door")),
                Some(open) => {
                    self.saved.door_open = open;
                    self.save_state();
                    self.tell_picks();
                    if !open {
                        self.end_watchers(|_| true);   // the door shut: no guest watches this plot any more
                    }
                }
            },
            // The room's line in the lobby's list of who is here: kept, and told to the lobby (null: none).
            "about" => {
                let about = match m.get("about") {
                    None | Some(Value::Null) => None,
                    Some(a) => match about_of(a) {
                        Some(a) => Some(a),
                        None => return self.page_error("bad_message", "about is { text (at most 200 characters), tags (at most 5: lower-case letters, digits and -) }, or null", Some("about")),
                    },
                };
                self.saved.about = about.clone();
                self.save_state();
                let _ = self.lobby.send(LobbyOut::Msg(ToLobby::About { about: about.clone() }));   // now, or with the next hello
                self.to_page(json!({ "type": "about", "about": about }));
            }
            // A web pass (3.1, SPEC 6.11): links the browser that opens it to this office (that browser then speaks for
            // it in the lobby, while it is in the lobby). Signed with its key, for its lobby, good once within PASS_TTL:
            // asked by its signed-in user's page; `qr`: as a QR code too (an SVG), for a phone to scan.
            "lobby_pass" => {
                let req = m.get("req").cloned().unwrap_or(Value::Null);
                let Some(url) = self.lobby_url.clone() else {
                    return self.page_error("no_lobby", "this workroom has no lobby (VISIT_LOBBY)", Some("lobby_pass"));
                };
                if !self.online {
                    return self.page_error("offline", "this workroom is not in the lobby now", Some("lobby_pass"));
                }
                let host = lobby_proto::authority(&url);
                // A QR code is shown on a screen anyone near may see: 2 minutes, not 10.
                let qr = m.get("qr") == Some(&json!(true));
                let ttl = if qr { 120 } else { lobby_proto::PASS_TTL };
                let expires = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs()) + ttl;
                let nonce = auth::secret::hex(&auth::secret::random(16));
                let signature = auth::secret::hex(&self.saved.key.sign(lobby_proto::pass_message(&host, &self.key, expires, &nonce).as_bytes()).to_bytes());
                let scheme = if url.starts_with("wss://") { "https" } else { "http" };
                let page = format!("{scheme}://{host}/#pass={}.{expires}.{nonce}.{signature}", self.key);
                let qr = qr.then(|| qr_svg(&page)).flatten();
                self.to_page(json!({ "type": "lobby_pass", "req": req, "url": page, "expires": expires, "qr": qr }));
            }
            // Worlds, linked browsers (3.1) and the lobby's operators (../../LOBBY.md): the lobby decides, and says.
            "group_create" | "group_join" | "group_leave" | "group_remove" | "group_unban" | "group_set" | "group_owner" | "group_delete" | "groups" | "admin"
            | "browser_code" | "browser_allow" | "browsers" | "browser_revoke" => {
                match serde_json::from_value::<ToLobby>(m) {
                    Ok(x) => self.to_lobby(x),
                    Err(_) => self.page_error("bad_message", &format!("{t} is not as it should be (../SPEC.md 5.2)"), Some(&t)),
                }
            }
            // 3.3: whose plots this office's world shows, its user's pick (at most MAX_PICKS): watched while they are
            // in the lobby, their door open to this office (a world's member's), drawn from their last landscape while
            // away. Unpicked: the watch ends, and nothing of it is kept, not where it was placed either.
            "pick" => match m.get("key").and_then(Value::as_str).filter(|k| state::key_ok(k)) {
                Some(k) if k == self.key => self.page_error("bad_message", "that is this office's own key", Some("pick")),
                Some(k) => {
                    let (k, name) = (k.to_string(), state::clip_name(m.get("name").and_then(Value::as_str).unwrap_or("")));
                    let full = self.saved.picks.len() >= state::MAX_PICKS;
                    match self.saved.picks.iter_mut().find(|g| g.key == k) {
                        Some(g) => g.name = name,
                        None if full => {
                            return self.page_error("too_many", &format!("at most {} rooms in your world at once: take one out first", state::MAX_PICKS), Some("pick"));
                        }
                        None => self.saved.picks.push(Pick { key: k.clone(), name }),
                    }
                    self.save_state();
                    self.tell_picks();
                    self.watch_declined.remove(&k);   // asked anew
                    self.watch_picks();
                }
                None => self.page_error("bad_message", "key is an office's key, as the lobby lists it", Some("pick")),
            },
            // 3.3: whom this office's user ignores in chat (a person by their office's key; a room's figure by that room's
            // key and its id), at most MAX_IGNORED: their lines never reach the page from now on, nor are kept.
            "ignore" => match state::ignored_from(&m) {
                Some(i) if i.key == self.key && i.npc.is_none() => self.page_error("bad_message", "that is this office's own key", Some("ignore")),
                Some(i) => {
                    let full = self.saved.ignored.len() >= state::MAX_IGNORED;
                    match self.saved.ignored.iter_mut().find(|x| x.key == i.key && x.npc == i.npc) {
                        Some(x) => x.name = i.name.clone(),
                        None if full => {
                            return self.page_error("too_many", &format!("at most {} ignored: let one go first", state::MAX_IGNORED), Some("ignore"));
                        }
                        None => self.saved.ignored.push(i.clone()),
                    }
                    self.save_state();
                    // Theirs kept from before: out of the mailbox (a whisper of theirs: read); the lines are left out of what a
                    // page is given (to_page) while they are ignored, and back once they are not.
                    let saved = &self.saved;
                    let unseen = self.unseen.len();
                    self.unseen.retain(|l| !saved.ignores(l));
                    if i.npc.is_none() {
                        let c = format!("person:{}", i.key);
                        if self.unseen_by.remove(&c).is_some() {
                            let _ = self.db.send(DbCmd::Seen { conversation: Some(c) });
                        }
                    }
                    if self.unseen.len() != unseen {
                        self.unseen_by.clear();
                        for l in &self.unseen {
                            *self.unseen_by.entry(l["conversation"].as_str().unwrap_or("").to_string()).or_default() += 1;
                        }
                    }
                    self.tell_ignored();
                    self.tell_unseen();
                }
                None => self.page_error("bad_message", "ignore is { key (an office's), npc? (a room's figure's id), name }", Some("ignore")),
            },
            "unignore" => {
                let (key, npc) = (m.get("key").and_then(Value::as_str).unwrap_or(""), m.get("npc").and_then(Value::as_str));
                self.saved.ignored.retain(|x| !(x.key == key && x.npc.as_deref() == npc));
                self.save_state();
                self.tell_ignored();
            }
            // 3.3 (#48): an action of a figure in the room this office's user is in, from its card: to that room's page.
            "act" => {
                let (figure, action) = (m.get("figure").and_then(Value::as_str), m.get("action").and_then(Value::as_str));
                let (Some(f), Some(t)) = (figure.filter(|f| link::possession_id_ok(f)), action.filter(|t| link::possession_id_ok(t))) else {
                    return self.page_error("bad_message", "act is { figure, action }: a figure's id and an interaction type", Some("act"));
                };
                match self.visiting() {
                    Some(v) => { let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "act": { "figure": f, "action": t } }))); }
                    None => {
                        let owner = self.inside_visit().map(|v| v.channel);
                        self.to_page(json!({ "type": "act", "owner": true, "channel": owner, "figure": f, "action": t }));
                    }
                }
            }
            "ignore_clear" => {
                self.saved.ignored.clear();
                self.save_state();
                self.tell_ignored();
            }
            "unpick" => match m.get("key").and_then(Value::as_str) {
                Some(k) => {
                    let k = k.to_string();
                    self.saved.picks.retain(|g| g.key != k);
                    let placed = self.saved.layout.len();
                    self.saved.layout.retain(|p| p.key != k);
                    self.save_state();
                    self.tell_picks();
                    if self.saved.layout.len() != placed {
                        self.tell_layout();
                    }
                    let watches: Vec<String> = self.visits.values()
                        .filter(|v| v.role == Role::Visitor && v.watch && !v.link && v.peer.get("key").and_then(Value::as_str) == Some(k.as_str()))
                        .map(|v| v.id.clone()).collect();
                    for id in watches {
                        self.to_lobby(end(&id, None));
                    }
                    self.forget_plot(&k);   // nor kept in this world while they are away
                }
                None => self.page_error("bad_message", "key is the picked office's key", Some("unpick")),
            },
            "join_lobby" => self.set_away(false),
            "host" => match m.get("three").and_then(Value::as_str).filter(|r| revision(r)) {
                Some(r) => {
                    let r = r.to_string();
                    self.set_hosting(Some(r));
                }
                None => self.page_error("bad_message", "three is the three.js revision of the scene hosted (digits)", Some("host")),
            },
            "knock" if self.hosting.is_none() => {
                self.page_error("not_hosting", "an office takes part once its page hosts its three.js scene (visit.host)", Some("knock"));
            }
            // 4.0 (#57): a page knocks only to talk (4.0), and answers only a knock to talk: no knocks to walk in, nor links.
            "knock" | "answer" => {
                let mut m = m;
                if t == "knock" && m.get("voice") != Some(&json!(true)) {
                    return self.page_error("not_supported", "Visit 4 has no knocks nor links: meet in a world (its members walk into each other's rooms)", Some("knock"));
                }
                if t == "answer" && !m.get("knock").and_then(Value::as_str).is_some_and(|k| self.knocks.contains_key(k)) {
                    return self.page_error("not_found", "no knock to talk with that id waiting on you", Some("answer"));
                }
                if t == "answer" {   // answered: no longer waiting, so not shown again to a page that reconnects
                    if let Some((k, _)) = m.get("knock").and_then(Value::as_str).and_then(|k| self.knocks.remove(k)) {
                        // 4.0: a knock to talk let in: the voice visit the lobby agrees for it is a call (visit_agreed)
                        if k.get("voice") == Some(&json!(true)) && m.get("accept") == Some(&json!(true)) {
                            if let (Some(id), Some(key)) = (k["from"]["id"].as_str(), k["from"]["key"].as_str()) {
                                self.voice_let.insert(id.to_string(), (key.to_string(), Instant::now()));
                            }
                        }
                    }
                } else if let Some(o) = m.as_object_mut() {
                    o.remove("watch");   // a page knocks to visit; watching is this visit-client's own (world)
                    if o.get("voice") == Some(&json!(true)) {   // 4.0: to talk
                        if !self.lobby_voice {
                            return self.page_error("not_supported", "this lobby carries no voice (an older lobby)", Some("knock"));
                        }
                        let to = o.get("to").and_then(Value::as_str).unwrap_or("").to_string();
                        self.voice_asked.retain(|_, at| at.elapsed() < VOICE_ASK);
                        self.voice_asked.insert(to, Instant::now());
                    } else {
                        o.remove("huddle");
                    }
                }
                match serde_json::from_value::<ToLobby>(m) {
                    Ok(x) => self.to_lobby(x),
                    Err(_) => self.page_error("bad_message", &format!("{t} is not as it should be (../SPEC.md 5.2)"), Some(&t)),
                }
            }
            // 4.0 (#59): what this room has a visitor in it do (an act, one of ACTS, or none) and whether it holds something of
            // this room's: told to that visitor's visit-client, for its page (its own body, posed the same).
            "perform" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let act = match m.get("act") {
                    None | Some(Value::Null) => Value::Null,
                    Some(a) => match a.get("clip").and_then(Value::as_str).filter(|c| ACTS.contains(c)) {
                        Some(clip) => json!({ "clip": clip, "loop": a.get("loop") == Some(&json!(true)),
                                              "secs": a.get("secs").and_then(Value::as_f64).filter(|s| *s > 0.0).map(|s| s.min(600.0)) }),
                        None => return self.page_error("bad_message", &format!("act is {{ clip, loop, secs }}, its clip one of {}", ACTS.join(", ")), Some("perform")),
                    },
                };
                let hold = m.get("hold") == Some(&json!(true));
                let Some(v) = self.visits.values_mut().find(|v| v.channel == ch && v.role == Role::Host && v.up && v.inside && !v.local) else {
                    return self.page_error("no_such_visit", "no visitor in this room on that channel", Some("perform"));
                };
                if v.performs.0.elapsed() >= Duration::from_secs(1) {
                    v.performs = (Instant::now(), 0);
                }
                v.performs.1 += 1;
                if v.performs.1 > PERFORMS {
                    return self.page_error("rate_limited", &format!("at most {PERFORMS} acts a second for one visitor"), Some("perform"));
                }
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "perform": { "act": act, "hold": hold } })));
            }
            // 4.0: a huddle in one of this office's worlds (on: its user starts one, in their room's voice, which carries
            // it; off: it ends, and so does every voice visit that joined it).
            "huddle" => {
                let (Some(group), Some(on)) = (m.get("group").and_then(Value::as_str).map(str::to_string), m.get("on").and_then(Value::as_bool)) else {
                    return self.page_error("bad_message", "huddle is { group, on }", Some("huddle"));
                };
                if on && !self.lobby_voice {
                    return self.page_error("not_supported", "this lobby carries no voice (an older lobby)", Some("huddle"));
                }
                self.to_lobby(ToLobby::GroupHuddle { group: Some(group.clone()), on });
                if on {
                    self.huddle = Some(group);
                    self.voice_join(Some(self.key.clone()));
                } else {
                    self.huddle_over(&group);
                }
            }
            // 4.0: a knock of this office's withdrawn (a call it rang, given up).
            "knock_cancel" => {
                let knock = m.get("knock").and_then(Value::as_str).map(str::to_string);
                self.to_lobby(ToLobby::KnockCancel { knock });
            }
            // The world (../WORLD.md): this office watches its picked plots for its page to draw, steps into one
            // (`enter`, where in its own terms) and out again.
            "world" => match m.get("on").and_then(Value::as_bool) {
                Some(on) => self.set_world(on),
                None => self.page_error("bad_message", "on must be true or false", Some("world")),
            },
            "enter" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let Some(id) = self.visits.values().find(|v| v.channel == ch && v.up && v.role == Role::Visitor && v.watch).map(|v| v.id.clone()) else {
                    return self.page_error("no_such_visit", "you are not watching on that channel", Some("enter"));
                };
                // In one room at a time: out of any other first (a visit there ends, as moving rooms ends one).
                let others: Vec<(String, bool)> = self.visits.values().filter(|v| v.role == Role::Visitor && v.inside && v.id != id).map(|v| (v.id.clone(), v.watch)).collect();
                for (other, watch) in others {
                    if watch {
                        self.step_out(&other);
                    } else {
                        self.to_lobby(end(&other, None));
                    }
                }
                // Where it steps in, in the room's terms; none (a link's "go there"): where the room puts newcomers.
                let pose = m.get("pose").filter(|p| p.is_object()).map(clamp_pose).unwrap_or(Value::Null);
                if let Some(v) = self.visits.get_mut(&id) {
                    v.inside = true;
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "enter": pose })));
                }
            }
            // Where this office has put a friend's plot in its world (kept in the state file).
            "place" => match state::place_from(&m) {
                Some(p) => {
                    let full = self.saved.layout.len() >= state::MAX_PLACES;
                    match self.saved.layout.iter_mut().find(|x| x.key == p.key) {
                        Some(x) => *x = p,
                        None if full => return self.page_error("too_many", &format!("at most {} places", state::MAX_PLACES), Some("place")),
                        None => self.saved.layout.push(p),
                    }
                    self.save_state();
                    self.tell_layout();
                }
                None => self.page_error("bad_message", "place needs a key, x and z (numbers) and turn (0-3)", Some("place")),
            },
            "unplace" => {
                let k = m.get("key").and_then(Value::as_str).unwrap_or("");
                self.saved.layout.retain(|p: &Place| p.key != k);
                self.save_state();
                self.tell_layout();
            }
            // Where this page has put an office in its world, beside its own room (8.4): told to that office alone, over
            // a link with it either way (it sees where it is in your world, and nothing else of it); null: not in it.
            "seen" => {
                let Some(key) = m.get("key").and_then(Value::as_str).filter(|k| state::key_ok(k)).map(str::to_string) else {
                    return self.page_error("bad_message", "key is an office's key", Some("seen"));
                };
                let seen = match m.get("at").filter(|a| !a.is_null()).map(seen_from) {
                    Some(Some(s)) => s,
                    Some(None) => return self.page_error("bad_message", "at is { dx, dz (m, within 100 km), turn (0-3), size ([w, d] m) } or null", Some("seen")),
                    None => Value::Null,
                };
                if seen.is_null() {
                    self.seen_out.remove(&key);
                } else if self.seen_out.len() >= state::MAX_PLACES && !self.seen_out.contains_key(&key) {
                    return self.page_error("too_many", &format!("at most {} offices", state::MAX_PLACES), Some("seen"));
                } else {
                    self.seen_out.insert(key.clone(), seen.clone());
                }
                self.to_peer(&key, json!({ "seen": seen }));   // with no link now, the next one tells them
            }
            // Avatars (8.6). The page's own (its frame, or `avatar_clear`): kept in memory, to go to hosts that ask. A
            // host's page asks for a visitor's (`avatar_want`). Whose look visitors have here (`avatars`: by default;
            // `avatar_use`: for a key, or back to the default): kept in the state file.
            "avatar_clear" => self.avatar = None,
            // Possessions (8.11): what this office's user owns and brings along, as its room makes them (Visit has none
            // of its own). Every host it visits is told what they are; a host's page asks for one's model
            // (`possession_want`), which goes as it came, its frame (by its slot in this list).
            "possessions" => match possessions_from(m.get("list")) {
                Some(list) => {
                    let old = std::mem::take(&mut self.possessions);
                    self.possessions = list.into_iter().map(|p| {
                        let gz = old.iter().find(|(o, _)| o["id"] == p["id"]).and_then(|(_, gz)| gz.clone());
                        (p, gz)
                    }).collect();
                    let said = json!({ "possessions": self.possessions.iter().map(|(p, _)| p.clone()).collect::<Vec<_>>() });
                    for v in self.visits.values().filter(|v| v.role == Role::Visitor && v.up) {
                        let _ = v.cmd.try_send(LinkCmd::Ctl(said.clone()));
                    }
                }
                None => self.page_error("bad_message", &format!("list is at most {MAX_POSSESSIONS} possessions: {{ id, name, mount, fly, speed, lift }}"), Some("possessions")),
            },
            "possession_want" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let Some(id) = m.get("id").and_then(Value::as_str).filter(|id| link::possession_id_ok(id)) else {
                    return self.page_error("bad_message", "id is a possession's", Some("possession_want"));
                };
                match self.visits.values().find(|v| v.channel == ch && v.role == Role::Host && v.up) {
                    Some(v) => { let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "possession_want": id }))); }
                    None => self.page_error("no_such_visit", "no visit to you on that channel", Some("possession_want")),
                }
            }
            // A phone (8.8): this room has one (or not), and a call on one of its visits: ring, answer, hang up.
            "phone" => match m.get("on").and_then(Value::as_bool) {
                Some(on) => self.set_phone(on),
                None => self.page_error("bad_message", "on must be true or false", Some("phone")),
            },
            "call" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let Some(act) = m.get("act").and_then(Value::as_str).filter(|a| ["ring", "answer", "hangup"].contains(a)).map(str::to_string) else {
                    return self.page_error("bad_message", "act is \"ring\", \"answer\" or \"hangup\"", Some("call"));
                };
                match self.visits.values_mut().find(|v| v.channel == ch && v.up) {
                    Some(v) if v.voice => {   // 4.0: a voice visit is its call: hung up, it ends
                        if act != "hangup" {
                            return self.page_error("bad_message", "a voice visit is a call already: hang up, or end it", Some("call"));
                        }
                        let id = v.id.clone();
                        return self.to_lobby(end(&id, None));
                    }
                    Some(v) if act == "answer" && v.ring != Some(false) => {   // (3.4.3: only a call they rang)
                        return self.page_error("no_call", "no call ringing from them on that visit", Some("call"));
                    }
                    Some(v) if v.peer_phone || act != "ring" => {   // only ringing needs their phone
                        let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "call": act })));
                        call_state(v, &act, true);
                    }
                    Some(_) => return self.page_error("no_phone", "their room has no phone", Some("call")),
                    None => return self.page_error("no_such_visit", "no visit on that channel", Some("call")),
                }
                self.tell_near();
            }
            // A call on this office's phone's speaker (everyone near the phone hears it, and is heard in it), or back
            // to the ear (8.8).
            "speaker" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let Some(on) = m.get("on").and_then(Value::as_bool) else {
                    return self.page_error("bad_message", "on must be true or false", Some("speaker"));
                };
                match self.visits.values_mut().find(|v| v.channel == ch && v.up && v.call_on) {
                    Some(v) => v.speaker = on,
                    None => return self.page_error("no_call", "no call on that channel", Some("speaker")),
                }
                self.tell_near();
            }
            // A friend's plot in this office's world, kept to draw while they are away (8.10): who, where its plot is
            // (in its terms), its unit. Drawn from its last landscape (`plot_load`: sent as `landscape`, channel 0);
            // let go (`plot_forget`) as the page says.
            "plot_keep" => match Kept::from_json(&m) {
                Some(k) => self.lands.keep_plot(k),
                None => self.page_error("bad_message", "key, name, plot { center, size }, unit", Some("plot_keep")),
            },
            "plot_load" => {
                let key = m.get("key").and_then(Value::as_str).unwrap_or("").to_string();
                match self.lands.latest(&key).filter(|_| self.lands.is_kept(&key)) {
                    Some(gz) => self.land_to_page(0, &key, gz),
                    None => self.page_error("not_found", "no plot kept for that key", Some("plot_load")),
                }
            }
            "plot_forget" => {
                let key = m.get("key").and_then(Value::as_str).unwrap_or("").to_string();
                self.forget_plot(&key);
            }
            // A line this office's user says (8.9): to everyone in the room they are in, and nobody else.
            // Where it goes (`to`): none (or "here"), everyone who can see this office's user (the room they are in, and
            // those who have it in their worlds or corners); "room", this office's room's chat (everyone in it, and every
            // office linked with it); { key }, a linked office's room's chat; { whisper }, to the user of the office with
            // that id alone, through the lobby; "lobby", everyone in the lobby.
            "say" => {
                let (media, links) = media::attachments(&m);
                let text = m.get("text").and_then(Value::as_str).map(said_text).unwrap_or_default();
                if text.is_empty() && media.is_empty() && links.is_empty() {
                    return self.page_error("bad_message", &format!("text is 1 to {SAY_LEN} characters, or the line has pictures or links"), Some("say"));
                }
                let shares = !media.is_empty() || !links.is_empty();
                let to = m.get("to").filter(|t| !t.is_null() && t.as_str() != Some("here"));
                let lobby = to.and_then(Value::as_str) == Some("lobby");
                let room = to.and_then(Value::as_str) == Some("room");
                // { group }: one of this office's world groups, everyone in it (../../LOBBY.md).
                let group = to.and_then(|t| t.get("group")).and_then(Value::as_str).and_then(|g| self.groups.get(&g.to_ascii_lowercase()))
                    .and_then(|g| g.get("name")).and_then(Value::as_str).map(str::to_string);
                if to.and_then(|t| t.get("group")).is_some() && group.is_none() {
                    return self.page_error("not_found", "this office is in no group of that name", Some("say"));
                }
                let key = to.and_then(|t| t.get("key")).and_then(Value::as_str).filter(|k| state::key_ok(k)).map(str::to_string);
                // 3.3 (#48): { figure }: a whisper to one of the figures of the room this office's user is in, for that room's
                // page alone (`addressed`); and, said aloud, the figure or person the line is meant for (`target`).
                let figure = to.and_then(|t| t.get("figure")).and_then(Value::as_str).filter(|id| link::possession_id_ok(id)).map(str::to_string);
                if to.and_then(|t| t.get("figure")).is_some() && figure.is_none() {
                    return self.page_error("bad_message", "figure is a room's figure's id (1 to 40 letters, digits, _ . or -)", Some("say"));
                }
                let target = match m.get("target").filter(|t| !t.is_null()) {
                    None => None,
                    Some(t) => match target_from(t) {
                        Some(t) if to.is_none() => Some(t),
                        _ => return self.page_error("bad_message", "target is { kind: \"figure\" | \"person\", id }, on a line said aloud (no to)", Some("say")),
                    },
                };
                if figure.is_some() && shares {
                    return self.page_error("not_supported", "a whisper to a figure is text only", Some("say"));
                }
                // { whisper }: an office's id, or its key (0.22.0): by it, one in the lobby that takes whispers, else
                // someone away, for whom this office keeps it (text only) until they and it are both in the lobby.
                let w = to.and_then(|t| t.get("whisper")).and_then(Value::as_str);
                let away = w.filter(|k| state::key_ok(k) && self.whisper_id(k).is_none()).map(str::to_string);
                let whisper = w.and_then(|w| if state::key_ok(w) { self.whisper_id(w) } else { Some(w.to_string()) }).filter(|id| !id.is_empty() && id.len() <= 40);
                if key.as_ref().is_some_and(|k| self.banned_by.get(k).is_some_and(|b| b.2)) {
                    return self.page_error("banned", "that room keeps you out of its chat", Some("say"));
                }
                if to.is_some() && !lobby && !room && key.is_none() && whisper.is_none() && away.is_none() && group.is_none() && figure.is_none() {
                    return self.page_error("bad_message", "to is \"here\", \"room\", \"lobby\", { key } of an office linked with this one, { whisper } an office's id or key, { group } one of this office's groups, or { figure } one of the room's figures", Some("say"));
                }
                if let Some(k) = away {
                    let name = to.and_then(|t| t.get("name")).and_then(Value::as_str).map(state::clip_name).map(|n| n.trim().to_string()).unwrap_or_default();
                    return self.hold(k, name, text, shares);
                }
                if (lobby || group.is_some()) && shares {
                    return self.page_error("not_supported", "the lobby carries text only: pictures and links go to rooms, and to those you are linked with", Some("say"));
                }
                // A whisper with pictures or links goes over a link with them, not through the lobby (8.12).
                let direct = whisper.as_deref().filter(|_| shares).map(|id| self.office_key(id).filter(|k| self.visits.values().any(|v| v.up && v.peer.get("key").and_then(Value::as_str) == Some(k.as_str()))));
                if let Some(None) = direct {
                    return self.page_error("no_link", "pictures and links go only to someone you are linked with", Some("say"));
                }
                if (lobby || group.is_some() || (whisper.is_some() && direct.is_none())) && !(self.online && self.lobby_chat) {
                    return self.page_error("not_supported", "this lobby takes no chat (or this office is not in it)", Some("say"));
                }
                if let Some(k) = &key {
                    if !self.visits.values().any(|v| v.up && v.peer.get("key").and_then(Value::as_str) == Some(k.as_str())) {
                        return self.page_error("no_link", "no link with that office, either way", Some("say"));
                    }
                }
                if !counted(&mut self.says) {
                    return self.page_error("rate_limited", &format!("at most {SAYS} lines every {} s", SAY_WINDOW.as_secs()), Some("say"));
                }
                // As it goes (its pictures and links with it), and as it is kept (a lobby line comes back from the
                // lobby, and is kept then).
                let mut line = json!({ "key": self.key, "name": self.my_name(), "text": text });
                attach(&mut line, media, links);
                let mut mine = line.clone();
                mine["type"] = json!("chat");
                mine["scope"] = json!("say");
                mine["mine"] = json!(true);
                if let Some(id) = whisper {
                    let o = self.lobby_view.as_ref().and_then(|l| l.get("offices")).and_then(Value::as_array)
                        .and_then(|os| os.iter().find(|o| o.get("id").and_then(Value::as_str) == Some(id.as_str())).cloned()).unwrap_or(Value::Null);
                    mine["scope"] = json!("whisper");
                    mine["to"] = json!({ "id": id, "key": o.get("key"), "name": o.get("name") });
                    mine["conversation"] = json!(format!("person:{}", o.get("key").and_then(Value::as_str).map_or_else(|| format!("id:{id}"), str::to_string)));
                    match direct.flatten() {
                        Some(k) => {
                            let mut w = json!({ "text": text });
                            attach(&mut w, line["media"].as_array().cloned().unwrap_or_default(), line["links"].as_array().cloned().unwrap_or_default());
                            self.to_peer(&k, json!({ "whisper": w }));
                        }
                        None => self.to_lobby(ToLobby::Whisper { to: Some(id), text: Some(text) }),
                    }
                } else if let Some(k) = key {
                    let name = self.visits.values().find(|v| v.peer.get("key").and_then(Value::as_str) == Some(k.as_str())).and_then(|v| v.peer.get("name").cloned());
                    mine["scope"] = json!("sim");
                    mine["to"] = json!({ "key": k, "name": name });
                    mine["conversation"] = json!(format!("room:{k}"));
                    let mut ctl = json!({ "sim_say": text });
                    attach(&mut ctl, line["media"].as_array().cloned().unwrap_or_default(), line["links"].as_array().cloned().unwrap_or_default());
                    self.to_peer(&k, ctl);
                } else if lobby {
                    return self.to_lobby(ToLobby::Chat { text: Some(text) });
                } else if let Some(g) = group {
                    return self.to_lobby(ToLobby::GroupSay { group: Some(g), text: Some(text) });   // kept as it comes back
                } else if room {
                    mine["scope"] = json!("sim");
                    mine["to"] = json!({ "key": self.key, "name": self.office.get("name") });
                    mine["conversation"] = json!(format!("room:{}", self.key));
                    self.room_said(&line, None);
                } else if let Some(fid) = figure {
                    // To the room this office's user is in: another's (its visit-client gives it to its page alone), or
                    // their own (to its page here). Kept here as theirs, as a whisper is: nobody else has it.
                    let fname = to.and_then(|t| t.get("name")).and_then(Value::as_str).map(state::clip_name).map(|n| n.trim().to_string()).unwrap_or_default();
                    let there = self.visiting().map(|v| (v.cmd.clone(), v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string()));
                    let room_key = there.as_ref().map_or_else(|| self.key.clone(), |t| t.1.clone());
                    mine["scope"] = json!("whisper");
                    mine["to"] = json!({ "key": room_key, "npc": fid, "name": fname });
                    mine["conversation"] = json!(format!("room:{room_key}"));
                    match there {
                        Some((cmd, _)) => { let _ = cmd.try_send(LinkCmd::Ctl(json!({ "tell": text, "figure": fid }))); }
                        None => {
                            let from = json!({ "owner": true, "key": self.key, "name": self.my_name() });
                            self.addressed(&fid, from, &text, true);
                        }
                    }
                } else {
                    let host = self.visiting().and_then(|v| v.peer.get("key").and_then(Value::as_str)).unwrap_or(&self.key).to_string();
                    mine["conversation"] = json!(format!("room:{host}"));
                    if let Some(t) = &target {
                        line["target"] = t.clone();
                        mine["target"] = t.clone();
                        if self.visiting().is_none() && t["kind"] == "figure" {   // a line in this room meant for its figure
                            let from = json!({ "owner": true, "key": self.key, "name": self.my_name() });
                            self.addressed(t["id"].as_str().unwrap_or(""), from, &text, false);
                        }
                    }
                    self.say_out(line);
                }
                self.keep_chat(mine);
            }
            // A line one of this room's own figures says (8.9: a receptionist, a guide; none of them a person): `id` names
            // it (its object in the room's scene is `visit:npc:<id>`), `name` is what it goes by. Said in this office's
            // room, to everyone who can see it (its visitors, and those who have it in their worlds or corners), and kept
            // in its chat, never as this office's user: their own limit is untouched, each figure has one of its own.
            "say_as" => {
                let id = m.get("id").and_then(Value::as_str).filter(|id| link::possession_id_ok(id)).map(str::to_string);
                let name = m.get("name").and_then(Value::as_str).map(state::clip_name).map(|n| n.trim().to_string()).filter(|n| !n.is_empty());
                let text = m.get("text").and_then(Value::as_str).map(said_text).unwrap_or_default();
                let (Some(id), Some(name)) = (id, name) else {
                    return self.page_error("bad_message", "id is 1 to 40 letters, digits, _ . or -, and name is not empty", Some("say_as"));
                };
                if text.is_empty() {
                    return self.page_error("bad_message", &format!("text is 1 to {SAY_LEN} characters"), Some("say_as"));
                }
                // 3.3 (#48): `to`, to one person alone: "owner" (this office's user), or a visit's id in this room.
                let to = m.get("to").filter(|t| !t.is_null());
                let alone = match to {
                    None => None,
                    Some(t) if t == "owner" => Some(None),
                    Some(t) => match t.as_str().and_then(|vid| self.visits.values().find(|v| v.id == vid && v.up && v.role == Role::Host && !v.local)) {
                        Some(v) => Some(Some(v.cmd.clone())),
                        None => return self.page_error("not_found", "to is \"owner\" or the id of a visit in this room", Some("say_as")),
                    },
                };
                if !counted(self.npc_says.entry(id.clone()).or_insert((Instant::now(), 0))) {
                    return self.page_error("rate_limited", &format!("at most {SAYS} lines every {} s from each", SAY_WINDOW.as_secs()), Some("say_as"));
                }
                let line = json!({ "key": self.key, "name": name, "text": text, "npc": id });
                if let Some(alone) = alone {   // a whisper from the figure: no bubble, nobody else
                    match alone {
                        Some(cmd) => { let _ = cmd.try_send(LinkCmd::Ctl(json!({ "said": line, "whisper": true }))); }
                        None => {
                            let mut chat = line;
                            chat["type"] = json!("chat");
                            chat["scope"] = json!("whisper");
                            chat["conversation"] = json!(format!("room:{}", self.key));
                            self.to_page(chat);
                        }
                    }
                    return;
                }
                for v in self.visits.values().filter(|v| v.up && v.role == Role::Host && !v.local && !self.chat_banned(v.peer.get("key").and_then(Value::as_str).unwrap_or(""))) {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "said": line })));
                }
                let mut chat = line;
                chat["type"] = json!("chat");
                chat["conversation"] = json!(format!("room:{}", self.key));
                self.to_page(chat);
            }
            // 3.4 (#36): a sound of this room's own, once, from a point in it (`at`, its units): to everyone in it (its
            // visitors, the watchers who stepped in, and its own user, at home on the engine), each to play by their
            // distance and side. Its clip (`id`) is one of the room's (its page put it here); at most SOUNDS_PER_SECOND.
            "sound_at" => {
                let id = m.get("id").and_then(Value::as_str).filter(|id| media::id_ok(id)).map(str::to_string);
                let at = m.get("at").and_then(Value::as_array).filter(|a| a.len() == 3)
                    .and_then(|a| a.iter().map(|x| x.as_f64().filter(|x| x.is_finite() && x.abs() <= 1e5)).collect::<Option<Vec<f64>>>());
                let (Some(id), Some(at)) = (id, at) else {
                    return self.page_error("bad_message", "sound_at is { id (a sound's, 64 hex), at: [x, y, z] (in the room's units) }", Some("sound_at"));
                };
                if self.sounds_at.0.elapsed() >= Duration::from_secs(1) {
                    self.sounds_at = (Instant::now(), 0);
                }
                if self.sounds_at.1 >= SOUNDS_PER_SECOND {
                    return self.page_error("rate_limited", &format!("at most {SOUNDS_PER_SECOND} sounds a second"), Some("sound_at"));
                }
                self.sounds_at.1 += 1;
                let num = |k: &str, d: f64, lo: f64, hi: f64| m.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(d, |x| x.clamp(lo, hi));
                let sound = json!({ "id": id, "at": at, "volume": num("volume", 1.0, 0.0, 1.0), "ref": num("ref", 2.0, 0.1, 1000.0), "far": num("far", 30.0, 0.5, 10000.0) });
                for v in self.visits.values().filter(|v| v.up && v.role == Role::Host && v.inside && !v.local) {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "sound": sound })));
                }
                if let Some(ch) = self.visits.values().find(|v| v.up && v.local && v.role == Role::Visitor).map(|v| v.channel) {   // its own user, at home (the engine)
                    let mut s = sound;
                    s["type"] = json!("sound");
                    s["channel"] = json!(ch);
                    self.to_page(s);
                }
            }
            // Whether someone is looking at this page's chat now (8.9): a line meant for its user that comes meanwhile is
            // seen as it comes; else it waits in the mailbox until they have read it (`read`).
            "attention" => self.attention = m.get("on") == Some(&json!(true)),
            // This office's user has read what the mailbox has of a conversation (none: of every one).
            "read" => {
                let conversation = match m.get("conversation") {
                    None | Some(Value::Null) => None,
                    Some(c) => match c.as_str().filter(|c| conversation_ok(c)) {
                        Some(c) => Some(c.to_string()),
                        None => return self.page_error("bad_message", "conversation is room:<key>, person:<key> or lobby (or none: all of them)", Some("read")),
                    },
                };
                let had = self.unseen_by.len();
                match &conversation {
                    Some(c) => {
                        self.unseen_by.remove(c);
                        self.unseen.retain(|l| l.get("conversation").and_then(Value::as_str) != Some(c.as_str()));
                    }
                    None => {
                        self.unseen_by.clear();
                        self.unseen.clear();
                    }
                }
                if self.unseen_by.len() != had {
                    let _ = self.db.send(DbCmd::Seen { conversation });
                }
                self.tell_unseen();   // (the mailbox as it is now, read or not)
            }
            // A whisper left for someone away, taken back before it went.
            "unhold" => {
                let id = m.get("id").and_then(Value::as_str).unwrap_or("");
                if let Some(i) = self.held.iter().position(|h| h.id == id) {
                    let h = self.held.remove(i);
                    let _ = self.db.send(DbCmd::Unhold(h.id));
                    self.tell_held();
                }
            }
            // The name this office's user goes by (8.9; null: none, this office's own): told to every office linked
            // with it, and to each that links.
            "user" => {
                let name = m.get("name").and_then(Value::as_str).map(state::clip_name).map(|n| n.trim().to_string()).filter(|n| !n.is_empty());
                if name == self.user_name {
                    return;
                }
                self.user_name = name.clone();
                for v in self.visits.values().filter(|v| v.up) {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "user": { "name": name } })));
                }
                self.tell_near();
                self.tell_voice_members();
            }
            // A picture the page would show (8.12): sent to it (a binary message, never dropped) once this office has
            // it, or `media_none`.
            "media_get" => match m.get("id").and_then(Value::as_str).filter(|id| media::id_ok(id)) {
                Some(id) => {
                    // 3.4 (#36): `channel`, the room it is from (a room's sound, as its scene names it): asked of that
                    // room's host if this visit-client does not have it, as a line's picture is (want_media).
                    let from = m.get("channel").and_then(Value::as_u64)
                        .and_then(|ch| self.visits.values().find(|v| v.up && v.role == Role::Visitor && !v.local && u64::from(v.channel) == ch)).map(|v| v.id.clone());
                    if let Some(vid) = from.filter(|_| !self.media_asked.contains_key(id)) {
                        self.media_asked.insert(id.to_string(), (Instant::now(), vid.clone(), false));
                        let _ = self.db.send(DbCmd::HasMedia { id: id.to_string(), visit: vid });
                    }
                    let _ = self.db.send(DbCmd::GetMedia { id: id.to_string(), to: MediaTo::Page });
                }
                None => self.page_error("bad_message", "id is a picture's id (64 hex characters)", Some("media_get")),
            },
            // A link's preview, made here (8.12: fetched once, by its sender): `preview` when made.
            "preview" => {
                let req = m.get("req").cloned().unwrap_or(Value::Null);
                let Some(url) = m.get("url").and_then(Value::as_str).filter(|u| media::url_ok(u)).map(str::to_string) else {
                    return self.page_error("bad_message", "url is an http or https link", Some("preview"));
                };
                let me = self.me.clone();
                tokio::task::spawn_blocking(move || {
                    let found = media::preview(&url);
                    let _ = me.send(Ev::Preview { req, url, found });
                });
            }
            // Lines before a moment (8.12: the chat's history, from the database), newest last.
            "history" => {
                let req = m.get("req").cloned().unwrap_or(Value::Null);
                let before = m.get("before").and_then(Value::as_i64).unwrap_or(i64::MAX);
                let limit = m.get("limit").and_then(Value::as_i64).unwrap_or(50).clamp(1, 200);
                let _ = self.db.send(DbCmd::History { req, before, limit });
            }
            // The pictures, or the links, shared in a conversation (8.12), newest first.
            "shared" => {
                let req = m.get("req").cloned().unwrap_or(Value::Null);
                let Some(conversation) = m.get("conversation").and_then(Value::as_str).filter(|c| conversation_ok(c)) else {
                    return self.page_error("bad_message", "conversation is room:<key>, person:<key> or lobby", Some("shared"));
                };
                let kind = match m.get("kind").and_then(Value::as_str) {
                    Some("links") => "links",
                    Some("voice") => "voice",
                    _ => "media",
                };
                let before = m.get("before").and_then(Value::as_i64).unwrap_or(i64::MAX);
                let limit = m.get("limit").and_then(Value::as_i64).unwrap_or(60).clamp(1, 200);
                let _ = self.db.send(DbCmd::Shared { req, conversation: conversation.to_string(), kind, before, limit });
            }
            // Where everyone in this office's room is (8.7), in metres, as its page has them: its own user (null:
            // elsewhere), its phone, and each visitor in it ([channel, x, y, z]). Who hears whom here goes by it.
            "voice_where" => {
                let role = self.replying_role();
                if role != PageRole::User {   // the room's word: where it has everyone in it (its owner among them: me)
                    self.voice.me = spot(m.get("me"));
                    self.voice.phone = spot(m.get("phone"));
                    self.voice.visitors = m.get("visitors").and_then(Value::as_array).into_iter().flatten().take(256).filter_map(|e| {
                        let a = e.as_array().filter(|a| a.len() == 4)?;
                        Some((u16::try_from(a[0].as_u64()?).ok()?, spot(Some(&json!(a[1..])))?))
                    }).collect();
                }
                if role != PageRole::Host {   // the person's world: where it has its friends at home in their own rooms (plots in it): [key, x, y, z]
                    self.voice.world = m.get("world").and_then(Value::as_array).into_iter().flatten().take(64).filter_map(|e| {
                        let a = e.as_array().filter(|a| a.len() == 4)?;
                        Some((a[0].as_str().filter(|k| state::key_ok(k))?.to_string(), spot(Some(&json!(a[1..])))?))
                    }).collect();
                }
                self.world_near();
                self.tell_near();
            }
            // The room voice this office's user is in (8.7): a linked office's room's (its key), their own (this
            // office's key), or none (null).
            "voice_join" => {
                let key = m.get("key").and_then(Value::as_str).map(str::to_string);
                if let Some(k) = key.as_ref().filter(|k| **k != self.key) {
                    if !self.visits.values().any(|v| v.up && v.peer.get("key").and_then(Value::as_str) == Some(k.as_str())) {
                        return self.page_error("no_link", "no link with that office, either way", Some("voice_join"));
                    }
                    if self.banned_by.get(k).is_some_and(|b| b.1) {
                        return self.page_error("banned", "that room keeps you out of its voice", Some("voice_join"));
                    }
                }
                self.voice_join(key);
            }
            // One out of this office's room's voice, now (they may come back).
            "voice_kick" => {
                let key = m.get("key").and_then(Value::as_str).unwrap_or("").to_string();
                if self.voice_members.remove(&key).is_some() {
                    self.to_peer(&key, json!({ "voice_out": "kicked" }));
                    self.tell_voice_members();
                }
            }
            // Someone kept out of this office's room's voice, its chat, both, or neither (8.9): kept until lifted.
            "ban" => {
                let Some(key) = m.get("key").and_then(Value::as_str).filter(|k| state::key_ok(k) && **k != self.key).map(str::to_string) else {
                    return self.page_error("bad_message", "key is another office's key", Some("ban"));
                };
                let (voice, chat) = (m.get("voice") == Some(&json!(true)), m.get("chat") == Some(&json!(true)));
                let name = state::clip_name(m.get("name").and_then(Value::as_str)
                    .or_else(|| self.visits.values().find(|v| v.peer.get("key").and_then(Value::as_str) == Some(key.as_str())).and_then(|v| v.peer.get("name")).and_then(Value::as_str))
                    .unwrap_or(""));
                self.saved.bans.retain(|b| b.key != key);
                if voice || chat {
                    if self.saved.bans.len() >= state::MAX_PEOPLE {
                        return self.page_error("too_many", "at most 256 kept out", Some("ban"));
                    }
                    self.saved.bans.push(state::Ban { key: key.clone(), name: name.clone(), voice, chat });
                }
                let _ = self.db.send(DbCmd::Ban(state::Ban { key: key.clone(), name, voice, chat }));
                self.to_page(json!({ "type": "bans", "bans": self.saved.bans_json() }));
                self.to_peer(&key, json!({ "banned": { "voice": voice, "chat": chat } }));
                if voice && self.voice_members.remove(&key).is_some() {
                    self.to_peer(&key, json!({ "voice_out": "banned" }));
                    self.tell_voice_members();
                }
                self.tell_near();
            }
            "avatar_want" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                match self.visits.values().find(|v| v.channel == ch && v.role == Role::Host && v.up) {
                    Some(v) => { let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "avatar_want": true }))); }
                    None => self.page_error("no_such_visit", "no visit to you on that channel", Some("avatar_want")),
                }
            }
            // How this room draws a visitor (host side, 8.6): as itself or as this room's model, its eyes over its feet
            // (scene units), and why not as itself, if it would be. To the visitor, which shows it in its own world.
            "body" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let body = body_from(&m);
                match self.visits.values().find(|v| v.channel == ch && v.role == Role::Host && v.up) {
                    Some(v) => { let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "body": body }))); }
                    None => self.page_error("no_such_visit", "no visit to you on that channel", Some("body")),
                }
            }
            "avatars" => match state::use_from(m.get("default")) {
                Some(theirs) => {
                    self.saved.avatars_theirs = theirs;
                    self.saved.avatars_set = true;
                    self.saved.avatars_by.retain(|(_, t)| *t != theirs);   // the same as the default now: no longer apart
                    self.save_state();
                    self.tell_avatars();
                }
                None => self.page_error("bad_message", "default is \"mine\" or \"theirs\"", Some("avatars")),
            },
            "avatar_use" => {
                let Some(key) = m.get("key").and_then(Value::as_str).filter(|k| state::key_ok(k)).map(str::to_string) else {
                    return self.page_error("bad_message", "key is an office's key", Some("avatar_use"));
                };
                let use_ = state::use_from(m.get("use"));
                if use_.is_none() && !m.get("use").is_none_or(Value::is_null) {
                    return self.page_error("bad_message", "use is \"mine\", \"theirs\" or null (the default)", Some("avatar_use"));
                }
                self.saved.avatars_by.retain(|(k, _)| *k != key);
                if let Some(theirs) = use_.filter(|t| *t != self.saved.avatars_theirs) {
                    if self.saved.avatars_by.len() >= state::MAX_PEOPLE {
                        return self.page_error("too_many", &format!("at most {} apart", state::MAX_PEOPLE), Some("avatar_use"));
                    }
                    self.saved.avatars_by.push((key, theirs));
                }
                self.save_state();
                self.tell_avatars();
            }
            "exit" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                match self.visits.values().find(|v| v.channel == ch && v.role == Role::Visitor && v.watch).map(|v| v.id.clone()) {
                    Some(id) => self.step_out(&id),
                    None => self.page_error("no_such_visit", "you are not watching on that channel", Some("exit")),
                }
            }
            // The lobby renames the office if the name is free; if not, it stays as it was (and says why).
            "rename" => match m.get("name").and_then(Value::as_str) {
                Some(n) if crate::name_ok(n) => {
                    let n = n.to_string();
                    self.to_lobby(ToLobby::Rename { name: Some(n) });
                }
                _ => self.page_error("name_invalid", "a name is 1-24 letters, digits, spaces, _ . -, starting and ending with a letter or digit", Some("rename")),
            },
            "end" => {
                let visit = m.get("visit").and_then(Value::as_str).unwrap_or("");
                if visit == HOME_ID || self.visits.get(visit).is_some_and(|v| v.local) {
                    self.page_error("bad_message", "the owner's visit is not ended: it goes with the person's page", Some("end"));
                } else if self.visits.contains_key(visit) {
                    let v = visit.to_string();
                    self.to_lobby(end(&v, None));
                } else {
                    self.page_error("no_such_visit", "you are not in a visit with that id", Some("end"));
                }
            }
            "input" => {
                self.input_seq += 1;
                let mut input = clamp_input(&m);
                input["seq"] = json!(self.input_seq);
                if let Some(v) = self.inside_visit() {
                    let _ = v.cmd.try_send(LinkCmd::Input(input));
                }
            }
            // A click at the middle of the visitor's view, where it looks (8.3): to the host, on the control stream.
            "point" => {
                if let Some(v) = self.inside_visit() {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "point": clamp_point(&m) })));
                }
            }
            "scene_resync" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                match self.visits.values().find(|v| v.channel == ch && v.up && v.role == Role::Visitor) {
                    Some(v) => { let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "scene_resync": true }))); }
                    None => self.page_error("no_such_visit", "you are not visiting on that channel", Some("scene_resync")),
                }
            }
            "pose" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let state = m.get("state").cloned().unwrap_or(Value::Null);
                match self.visits.values_mut().find(|v| v.channel == ch && v.role == Role::Host) {
                    Some(v) if state.to_string().len() <= 2048 => v.host_state = Some(state),
                    Some(_) => self.page_error("bad_message", "a visitor's state is at most 2 KiB", Some("pose")),
                    None => self.page_error("no_such_visit", "no visit to you on that channel", Some("pose")),
                }
            }
            "keyframe" => {
                let ch = m.get("channel").and_then(Value::as_u64).unwrap_or(0) as u16;
                let Some(v) = self.visits.values().find(|v| v.channel == ch && v.up && v.role == Role::Visitor) else {
                    return self.page_error("no_such_visit", "you are not visiting on that channel", Some(&t));
                };
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "keyframe": true })));
            }
            other => {
                let other = other.to_string();
                self.page_error("unknown_type", &format!("no message type {other:?}"), Some(&other));
            }
        }
    }

    /// A delta of this office's scene for one of its visitors, or an edit of it.
    fn page_frame(&mut self, data: &[u8]) {
        if let Some((slot, bytes)) = frames::media_from_page(data) {   // a picture or a voice message to share (8.12): kept, and its id told
            return match media::shared(&bytes) {
                Some((id, mime)) => {
                    self.media_keep(&id, mime, &bytes);
                    self.to_page(json!({ "type": "media_ready", "slot": slot, "id": id, "mime": mime, "size": bytes.len() }));
                }
                None => self.to_page(json!({ "type": "error", "code": "bad_media", "ref": "media_put", "slot": slot,
                                              "message": format!("a picture is a PNG, JPEG, WebP or GIF of at most {} MB; a voice message, WebM, Ogg or MP4 of at most {} MB",
                                                                 media::MAX_MEDIA >> 20, media::MAX_VOICE >> 20) })),
            };
        }
        if let Some(gz) = frames::land_from_page(data) {   // this room's landscape: kept, and offered by its hash to every visitor
            return self.land_set(gz);
        }
        if let Some((to, audio)) = frames::audio_from_page(data) {   // this office's user's voice: this visit-client decides who hears it
            return self.voice_out(to, audio);
        }
        if let Some((slot, gz)) = frames::possession_from_page(data) {   // a possession's model: to every host that asked for it
            let Some((p, kept)) = self.possessions.get_mut(slot as usize) else { return };
            let id = p["id"].as_str().unwrap_or("").to_string();
            *kept = Some(gz.clone());
            for v in self.visits.values().filter(|v| v.role == Role::Visitor && v.up && v.possessions_asked.contains(&id)) {
                let _ = v.cmd.try_send(LinkCmd::Possession(id.clone(), gz.clone()));
            }
            return;
        }
        if let Some(gz) = frames::avatar_from_page(data) {   // this office's avatar: to every host that asked for it
            for v in self.visits.values().filter(|v| v.role == Role::Visitor && v.up && v.avatar_asked) {
                let _ = v.cmd.try_send(LinkCmd::Avatar(gz.clone()));
            }
            self.avatar = Some(gz);
            return;
        }
        if let Some((ch, gz)) = frames::scene_from_page(data) {
            let resync = match self.visits.values().find(|v| v.channel == ch && v.role == Role::Host && v.up) {
                Some(v) => v.cmd.try_send(LinkCmd::Scene(gz)).is_err(),
                None => return debug!(ch, "a scene edit for no visit"),
            };
            if resync {   // the link's queue is full: what follows would build on an edit it never got
                self.to_page(json!({ "type": "scene_resync", "channel": ch }));
            }
            return;
        }
        let Some((ch, frame)) = Frame::from_page(data) else { return debug!(len = data.len(), "a malformed delta from the page") };
        match self.visits.values().find(|v| v.channel == ch && v.role == Role::Host && v.up) {
            Some(v) => {
                if v.cmd.try_send(LinkCmd::Frame(frame)).is_err() {
                    debug!(ch, "the link is not taking deltas");
                }
            }
            None => debug!(ch, "a delta for no visit"),
        }
    }

    /// To the page it concerns (../../engine/SPEC.md 3.4): the room's, the person's, or both, by what it is; an answer,
    /// to the connection that asked; while a page is welcomed, to it alone.
    fn to_page(&mut self, mut v: Value) {
        let t = v.get("type").and_then(Value::as_str).unwrap_or("").to_string();
        // 3.3: someone its user ignores: their lines never reach the page (nor bubbles, nor the mailbox), and are not
        // kept; those kept from before are left out of what the page is given again.
        if t == "chat" && self.saved.ignores(&v) {
            return;
        }
        if matches!(t.as_str(), "chat_log" | "history") && !self.saved.ignored.is_empty() {
            if let Some(lines) = v.get_mut("lines").and_then(Value::as_array_mut) {
                let saved = &self.saved;
                lines.retain(|l| !saved.ignores(l));
            }
        }
        if t == "chat" {
            if v.get("conversation").is_none() {
                v["conversation"] = json!(self.conversation_of(&v));
            }
            if v.get("far") != Some(&json!(true)) {   // (3.4.2: a line said out of its user's hearing is not theirs to keep)
                self.keep_chat(v.clone());
            }
        }
        let text = v.to_string();
        if let Some(id) = self.only {
            if let Some(p) = self.page_by_id(id) {
                let _ = p.text.send(PageOut::Text(text));
            }
            return;
        }
        if matches!(t.as_str(), "error" | "media_ready") {
            if let Some((id, _)) = self.replying {
                if let Some(p) = self.page_by_id(id) {
                    let _ = p.text.send(PageOut::Text(text));
                }
                return;
            }
        }
        for p in self.pages_for(page_for(&v)) {
            let _ = p.text.send(PageOut::Text(text.clone()));   // gone: PageClosed follows
        }
    }

    /// A line of chat this office's user had, or said, with when (ms since 1970): kept in the database (8.12), and
    /// the last CHAT_KEPT here, for a page that comes back (8.9). One meant for them that comes while nobody is looking
    /// at its page's chat is in the mailbox until they have seen it.
    fn keep_chat(&mut self, mut line: Value) {
        line["at"] = json!(db::now());
        if line.get("conversation").is_none() {
            line["conversation"] = json!(self.conversation_of(&line));
        }
        if self.chat.len() >= CHAT_KEPT {
            self.chat.pop_front();
        }
        self.chat.push_back(line.clone());
        let unseen = !(self.user_page.is_some() && self.attention) && self.for_me(&line);
        if unseen {
            if self.unseen.len() >= UNSEEN_KEPT {
                self.unseen.pop_front();
            }
            self.unseen.push_back(line.clone());
            *self.unseen_by.entry(line["conversation"].as_str().unwrap_or("").to_string()).or_default() += 1;
        }
        let _ = self.db.send(DbCmd::Line { line, unseen });
        if unseen {
            self.tell_unseen();
        }
    }

    /// Whether a line is meant for this office's user (8.9: what the mailbox counts): whispered to them, said in their
    /// room's chat, or one that mentions them; never their own.
    fn for_me(&self, l: &Value) -> bool {
        let scope = l.get("scope").and_then(Value::as_str);
        if l.get("mine") == Some(&json!(true)) || (scope == Some("lobby") && l.get("id").is_some_and(|id| !id.is_null() && *id == self.office["id"])) {
            return false;
        }
        scope == Some("whisper")
            || (scope == Some("sim") && l.get("conversation").and_then(Value::as_str) == Some(format!("room:{}", self.key).as_str()))
            || l.get("text").and_then(Value::as_str).is_some_and(|t| mentions(t, &[self.user_name.as_deref(), self.office.get("name").and_then(Value::as_str)]))
    }

    /// The mailbox (8.9), to the page: how many lines its user has not seen, in all and in each conversation, and the
    /// newest of them.
    fn tell_unseen(&mut self) {
        let count: u64 = self.unseen_by.values().sum();
        let by: Vec<Value> = self.unseen_by.iter().map(|(c, n)| json!({ "conversation": c, "count": n })).collect();
        let lines: Vec<Value> = self.unseen.iter().cloned().collect();
        self.to_page(json!({ "type": "unseen", "count": count, "conversations": by, "lines": lines }));
    }

    // ---------- whispers left for someone away (8.9) ----------

    /// The id of the office in the lobby with this key, if it takes whispers.
    fn whisper_id(&self, key: &str) -> Option<String> {
        self.lobby_view.as_ref()?.get("offices")?.as_array()?.iter()
            .find(|o| o.get("key").and_then(Value::as_str) == Some(key) && o.get("whisper") == Some(&json!(true)))
            .and_then(|o| o.get("id")).and_then(Value::as_str).map(str::to_string)
    }

    /// A whisper for someone not in the lobby (their key), kept here, and in the database, until they and this office
    /// are both in it: text only (pictures, voice messages and links go over a link).
    fn hold(&mut self, key: String, name: String, text: String, shares: bool) {
        if shares {
            return self.page_error("no_link", "pictures, voice messages and links go only to someone you are linked with: a whisper kept for someone away is text only", Some("say"));
        }
        if self.held.len() >= HELD_MAX || self.held.iter().filter(|h| h.key == key).count() >= HELD_EACH {
            return self.page_error("too_many", &format!("at most {HELD_EACH} whispers kept for one person, {HELD_MAX} in all: they go once you are both in the lobby"), Some("say"));
        }
        if !counted(&mut self.says) {
            return self.page_error("rate_limited", &format!("at most {SAYS} lines every {} s", SAY_WINDOW.as_secs()), Some("say"));
        }
        let at = db::now();
        self.held_seq += 1;
        let h = Held { id: format!("{at}-{}", self.held_seq), key: key.clone(), name: name.clone(), text: text.clone(), at };
        let _ = self.db.send(DbCmd::Hold(h.clone()));
        self.held.push(h);
        let mine = json!({ "type": "chat", "scope": "whisper", "mine": true, "held": true, "key": self.key, "name": self.my_name(), "text": text,
                           "to": { "id": null, "key": key, "name": name }, "conversation": format!("person:{key}") });
        self.keep_chat(mine);
        self.tell_held();
    }

    /// The oldest whisper left for someone now in the lobby, passed on through it, if this office may say a line now
    /// (they count with its user's own): one at a time, every HELD_EVERY at most. Its page hears it went.
    fn deliver_held(&mut self) {
        if self.held.is_empty() || !(self.online && self.lobby_chat) || Instant::now() < self.held_next {
            return;
        }
        let Some((i, id)) = self.held.iter().enumerate().find_map(|(i, h)| self.whisper_id(&h.key).map(|id| (i, id))) else { return };
        if self.says.0.elapsed() < SAY_WINDOW && self.says.1 >= SAYS {
            return;   // its user's own lines first: this one in a moment
        }
        counted(&mut self.says);
        let h = self.held.remove(i);
        self.to_lobby(ToLobby::Whisper { to: Some(id), text: Some(h.text.clone()) });
        let _ = self.db.send(DbCmd::Unhold(h.id.clone()));
        self.held_next = Instant::now() + HELD_EVERY;
        self.to_page(json!({ "type": "held_sent", "id": h.id, "key": h.key, "name": h.name, "text": h.text, "at": h.at }));
        self.tell_held();
    }

    /// The whispers left for someone away, to the page.
    fn tell_held(&mut self) {
        let held: Vec<Value> = self.held.iter().map(Held::json).collect();
        self.to_page(json!({ "type": "held", "held": held }));
    }

    /// The conversation a line is in (8.12): a room's ("room:<its owner's key>": said in it, or in its chat), a
    /// person's ("person:<their key>": whispers), or the lobby's.
    fn conversation_of(&self, m: &Value) -> String {
        let peer = m.get("channel").and_then(Value::as_u64).and_then(|ch| self.visits.values().find(|v| u64::from(v.channel) == ch))
            .map(|v| (v.role, v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string()));
        match m.get("scope").and_then(Value::as_str) {
            Some("lobby") => "lobby".into(),
            Some("group") => format!("group:{}", m.get("group").and_then(Value::as_str).unwrap_or("").to_ascii_lowercase()),
            Some("whisper") => format!("person:{}", m.get("key").and_then(Value::as_str).filter(|k| !k.is_empty()).map_or_else(|| format!("id:{}", m["id"].as_str().unwrap_or("")), str::to_string)),
            Some("sim") if m.get("home") != Some(&json!(true)) => format!("room:{}", peer.map_or_else(|| self.key.clone(), |(_, k)| k)),
            Some("sim") => format!("room:{}", self.key),
            _ => match peer {
                Some((Role::Visitor, k)) => format!("room:{k}"),   // said in the room this office's user is in, or sees
                _ => format!("room:{}", self.key),
            },
        }
    }

    // ---------- pictures (8.12): kept in the database, asked for over the link a line came by ----------

    /// The pictures a line names that came over a link (`visit`): each this office does not have is asked of that
    /// link, once (meanwhile, whoever asks for it here waits for it).
    fn want_media(&mut self, line: &Value, visit: &str) {
        for id in media_ids(line) {
            if !self.media_asked.contains_key(&id) {
                self.media_asked.insert(id.clone(), (Instant::now(), visit.to_string(), false));
                let _ = self.db.send(DbCmd::HasMedia { id, visit: visit.to_string() });
            }
        }
    }

    fn db_has(&mut self, id: String, visit: &str, has: bool) {
        if has {
            self.media_asked.remove(&id);
            for to in self.media_waiting.remove(&id).unwrap_or_default() {
                let _ = self.db.send(DbCmd::GetMedia { id: id.clone(), to });
            }
        } else if let Some(a) = self.media_asked.get_mut(&id) {
            *a = (Instant::now(), visit.to_string(), true);
            if let Some(v) = self.visits.get(visit).filter(|v| v.up) {
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "media_want": id })));
            }
        }
    }

    /// A picture kept: in the database, and (the last few) here, for whoever asks while the database takes it.
    fn media_keep(&mut self, id: &str, mime: &str, bytes: &Bytes) {
        let _ = self.db.send(DbCmd::PutMedia { id: id.to_string(), mime: mime.to_string(), bytes: bytes.clone() });
        if self.media_recent.len() >= MEDIA_RECENT {
            self.media_recent.pop_front();
        }
        self.media_recent.push_back((id.to_string(), mime.to_string(), bytes.clone()));
    }

    /// A picture this office asked for, come: kept, and to whoever waits for it. One not asked for is dropped.
    fn media_in(&mut self, visit: &str, id: String, mime: String, bytes: Bytes) {
        if self.media_asked.remove(&id).is_none() {
            return debug!(visit, "a picture not asked for: dropped");
        }
        self.media_keep(&id, &mime, &bytes);
        for to in self.media_waiting.remove(&id).unwrap_or_default() {
            self.media_to(&to, &id, &mime, &bytes);
        }
    }

    /// The database's answer for a picture asked for: to whoever asked; none, and coming, they wait; none, and not
    /// coming, they are told (`media_none`).
    fn db_media(&mut self, id: String, to: MediaTo, found: Option<(String, Bytes)>) {
        let recent = self.media_recent.iter().find(|(i, _, _)| *i == id).map(|(_, m, b)| (m.clone(), b.clone()));
        match found.or(recent) {
            Some((mime, bytes)) => self.media_to(&to, &id, &mime, &bytes),
            None if self.media_asked.contains_key(&id) => self.media_waiting.entry(id).or_default().push(to),
            None => self.media_none_to(&to, &id),
        }
    }

    fn media_to(&mut self, to: &MediaTo, id: &str, mime: &str, bytes: &Bytes) {
        match to {
            MediaTo::Page => {
                if let Some(p) = &self.user_page {
                    let _ = p.text.send(PageOut::Binary(frames::media_to_page(id, mime, bytes)));
                }
            }
            MediaTo::Link(visit) => {
                if let Some(v) = self.visits.get(visit).filter(|v| v.up) {
                    let _ = v.cmd.try_send(LinkCmd::Media { id: id.to_string(), mime: mime.to_string(), bytes: bytes.clone() });
                }
            }
        }
    }

    fn media_none_to(&mut self, to: &MediaTo, id: &str) {
        match to {
            MediaTo::Page => self.to_page(json!({ "type": "media_none", "id": id })),
            MediaTo::Link(visit) => {
                if let Some(v) = self.visits.get(visit).filter(|v| v.up) {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "media_none": id })));
                }
            }
        }
    }

    /// A picture asked for that will not come (the other side has none, or did not answer in a minute): whoever
    /// waits for it is told.
    fn media_gone(&mut self, id: &str) {
        self.media_asked.remove(id);
        for to in self.media_waiting.remove(id).unwrap_or_default() {
            self.media_none_to(&to, id);
        }
    }

    fn media_expire(&mut self) {
        let old: Vec<String> = self.media_asked.iter().filter(|(_, (at, _, _))| at.elapsed() >= Duration::from_secs(60)).map(|(id, _)| id.clone()).collect();
        for id in old {
            self.media_gone(&id);
        }
    }

    /// A link's preview, made: its picture kept (a picture's id), and to the page.
    fn preview_in(&mut self, req: Value, url: String, found: Option<media::Preview>) {
        let Some(p) = found else {
            return self.to_page(json!({ "type": "preview", "req": req, "url": url, "none": true }));
        };
        let image = p.image.and_then(|b| {
            let (id, mime) = media::picture(&b, media::MAX_MEDIA)?;
            self.media_keep(&id, mime, &b);
            Some(json!({ "id": id, "mime": mime, "size": b.len() }))
        });
        self.to_page(json!({ "type": "preview", "req": req, "url": url, "title": p.title, "description": p.description, "site": p.site, "image": image }));
    }

    fn page_error(&mut self, code: &str, message: &str, re: Option<&str>) {
        let mut e = json!({ "type": "error", "code": code, "message": message });
        if let Some(r) = re {
            e["ref"] = json!(r);
        }
        self.to_page(e);
    }

    // ---------- the lobby ----------

    fn to_lobby(&mut self, m: ToLobby) {
        if self.online {
            let _ = self.lobby.send(LobbyOut::Msg(m));
        } else {
            let re = m.to_json().get("type").and_then(Value::as_str).map(str::to_string);
            if !self.signed_in {
                self.page_error("signed_out", "nobody is signed in on this workroom: it joins the lobby once someone is", re.as_deref());
            } else if self.saved.away {
                self.page_error("away", "this office is out of the lobby: join_lobby to come back", re.as_deref());
            } else {
                self.page_error("offline", "the lobby is not reachable right now", re.as_deref());
            }
        }
    }

    /// In or out of the lobby, as the page asks. Out: its visits end, its knocks go, the lobby hears bye,
    /// and it stays out (across restarts too, with a state file) until asked back.
    fn set_away(&mut self, away: bool) {
        if away == self.saved.away {
            return;
        }
        self.saved.away = away;
        self.save_state();
        if away {
            info!("leaving the lobby: the page asked");
            self.end_all("gone");
            self.knocks.clear();
            self.lobby_view = None;
            self.online = false;
            let _ = self.lobby.send(LobbyOut::Leave);
        } else {
            info!("joining the lobby again: the page asked");
            let _ = self.lobby.send(LobbyOut::Join);
        }
        self.to_page(json!({ "type": "offline", "away": away }));
    }

    /// Someone signed in on this workroom, or nobody any more (../../LOBBY.md 2, rule 3). Signed out, the office leaves
    /// the lobby as if its page had taken it out (its visits end, its knocks go), until someone signs in again.
    fn set_signed_in(&mut self, s: bool) {
        if s == self.signed_in {
            return;
        }
        self.signed_in = s;
        let _ = self.lobby.send(LobbyOut::SignedIn(s));
        if s {
            info!("someone signed in on this workroom: in the lobby");
        } else {
            info!("nobody signed in on this workroom any more: out of the lobby");
            self.end_all("gone");
            self.knocks.clear();
            self.lobby_view = None;
            self.groups.clear();
            self.groups_before.clear();
            self.groups_settle = None;
            if self.online {
                self.online = false;
                self.to_page(json!({ "type": "offline" }));
            }
        }
        self.to_page(json!({ "type": "signed_in", "signed_in": s }));
    }

    /// The keys of everyone in this office's world groups but itself: they may watch its plot through its open door.
    fn co_members(&self) -> HashSet<String> {
        self.groups.values().chain(self.groups_before.values()).flat_map(|g| g.get("members").and_then(Value::as_array).cloned().unwrap_or_default())
            .filter_map(|m| m.get("key").and_then(Value::as_str).map(str::to_string)).filter(|k| *k != self.key).collect()
    }

    /// Whether `key` is this office's guest (3.3): someone in one of its worlds. Through its open door, a guest
    /// watches its plot (puts it in their world) and walks in without asking; and it theirs.
    fn is_guest(&self, key: &str) -> bool {
        self.co_members().contains(key)
    }

    fn save_state(&self) {
        let Some(path) = &self.state_file else { return };
        if let Err(e) = self.saved.save(path) {
            warn!("could not keep this office's state in {}: {e}", path.display());
        }
    }

    fn door(&self) -> &'static str {
        if self.saved.door_open { "open" } else { "closed" }
    }

    fn tell_layout(&mut self) {
        let layout = self.saved.layout_json();
        self.to_page(json!({ "type": "layout", "layout": layout }));
    }

    // ---------- voice (8.7) and phones (8.8): who hears whom in this office's room, decided here ----------

    /// This office's user's voice, from its page, where it may go (`allowed`): in person (the room they are in:
    /// another's, to its owner, whose visit-client decides; their own, to each visitor near them; and across a plot's
    /// side, to each friend near them in both worlds), to a call (to the ear, always; on speaker, if they are near the
    /// phone), and to the room voice they are in. It goes each way it may: which way each hears it is theirs to
    /// choose (8.7: in person first, then a call, then a room voice).
    fn voice_out(&mut self, allowed: u16, voice: Bytes) {
        let away = self.visiting().map(|v| v.channel);
        let me = if away.is_some() { None } else { self.voice.me };
        let mut to: Vec<u16> = Vec::new();
        if allowed & frames::VOICE_ROOM != 0 {
            to.extend(away);
            if let Some(me) = me {
                to.extend(self.within(me, VOICE_RANGE + VOICE_MARGIN, None));
                for key in self.voice.world_told.iter().filter(|k| self.voice.world_heard.contains(*k) && !self.voice_banned(k)) {
                    to.extend(self.link_with(key));
                }
            }
        }
        if allowed & frames::VOICE_CALLS != 0 {
            let by_phone = me.zip(self.voice.phone).is_some_and(|(me, p)| flat(me, p) <= VOICE_RANGE);
            to.extend(self.visits.values().filter(|v| v.up && v.call_on && (!v.speaker || by_phone)).map(|v| v.channel));
        }
        to.sort_unstable();
        to.dedup();
        for v in self.visits.values().filter(|v| v.up && to.contains(&v.channel)) {
            let _ = v.cmd.try_send(LinkCmd::Audio(voice.clone()));
        }
        if allowed & frames::VOICE_CHANNEL != 0 {
            let Some(room) = self.room_voice.clone() else { return };
            let head = frames::relay_head(&self.key, self.my_name().as_str().unwrap_or(""), &voice);
            if room == self.key {   // this office's own room's voice: to everyone in it
                for key in self.voice_members.keys() {
                    self.voice_to(key, false, &head);
                }
            } else {
                self.voice_to(&room, true, &head);
            }
        }
    }

    /// A packet of voice over a link (`relayed`: someone else's, passed on, with who spoke), heard one way only (8.7):
    /// in person first, then on a call. In person: the room this office's user is in (its owner, or one passed on); a
    /// visitor in this room (to the page if this office's user is near them, to the other visitors near them, and to
    /// a call on speaker if they are near the phone); a friend across a plot's side, near in both worlds. A call's: to
    /// the page, unless its caller is heard in person; on speaker, also to this room's visitors near the phone, as the
    /// phone's. Else nowhere.
    fn voice_in(&mut self, id: &str, data: Bytes, relayed: bool) {
        let Some(v) = self.visits.get(id).filter(|v| v.up) else { return };
        let (ch, role, inside, call_on, speaker, visit) = (v.channel, v.role, v.inside, v.call_on, v.speaker, v.id.clone());
        let name = person(v).as_str().unwrap_or("").to_string();
        let key = v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string();
        let near = !relayed && self.in_person(&key);
        if role == Role::Visitor && inside {   // the room this office's user is in: its owner's voice, or one passed on
            let route = if call_on && !relayed && !near { frames::ROUTE_CALL } else { frames::ROUTE_NEAR };
            return self.voice_to_page(ch, route, &data, relayed);
        }
        if role == Role::Host && inside && !relayed && !self.voice_banned(&key) {   // a visitor in this room
            if let Some(at) = self.voice.visitors.get(&ch).copied() {   // where the room has them: not said yet, nobody hears
                if (!call_on || near) && self.voice.me.is_some_and(|me| flat(me, at) <= VOICE_RANGE + VOICE_MARGIN) {
                    self.voice_to_page(ch, frames::ROUTE_NEAR, &data, false);
                }
                let mut to = self.within(at, VOICE_RANGE + VOICE_MARGIN, Some(ch));
                if self.voice.phone.is_some_and(|p| flat(p, at) <= VOICE_RANGE) {   // a call on speaker hears those near the phone
                    to.extend(self.visits.values().filter(|c| c.up && c.call_on && c.speaker && c.channel != ch).map(|c| c.channel));
                }
                self.relay_to(&to, frames::relay_head(&visit, &name, &data));
            }
        } else if !relayed && self.seam(&key) && !self.voice_banned(&key) && (!call_on || near) {   // across a plot's side
            return self.voice_to_page(ch, frames::ROUTE_NEAR, &data, false);
        }
        if !call_on || near {   // no call; or its caller heard in person, and the call goes quiet for them
            return;
        }
        self.voice_to_page(ch, frames::ROUTE_CALL, &data, relayed);
        if let Some(phone) = self.voice.phone.filter(|_| speaker) {
            let head = if relayed {
                let Some((who, said, voice)) = frames::relay_parts(&data) else { return };
                frames::relay_head(&format!("phone:{who}"), &said, voice)
            } else {
                frames::relay_head("phone", &name, &data)
            };
            self.relay_to(&self.within(phone, VOICE_RANGE + VOICE_MARGIN, Some(ch)), head);
        }
    }

    /// A packet of a room voice (8.7). Up, from one in this office's room's voice: to everyone else in it, and to the
    /// page if this office's user is in it. Down, from the room whose voice this office's user is in: to the page.
    /// Each unless its speaker is heard another way (in person, or on a call).
    fn room_voice_in(&mut self, id: &str, up: bool, data: Bytes) {
        let Some(v) = self.visits.get(id).filter(|v| v.up) else { return };
        let ch = v.channel;
        let key = v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string();
        let name = person(v).as_str().unwrap_or("").to_string();
        let Some((who, _, voice)) = frames::relay_parts(&data) else { return };
        if up {
            if !self.voice_members.contains_key(&key) || self.voice_banned(&key) {
                return;
            }
            let head = frames::relay_head(&key, &name, voice);
            for m in self.voice_members.keys().filter(|m| **m != key) {
                self.voice_to(m, false, &head);
            }
            if self.room_voice.as_deref() == Some(self.key.as_str()) && !self.in_person(&key) && !self.on_call(&key) {
                self.voice_to_page(ch, frames::ROUTE_VOICE, &head, true);
            }
        } else if self.room_voice.as_deref() == Some(key.as_str()) && who != self.key && !self.in_person(&who) && !self.on_call(&who) {
            self.voice_to_page(ch, frames::ROUTE_VOICE, &data, true);
        }
    }

    /// A room voice's packet (who spoke, and the voice) to the office with this key, over one link up with it: to its
    /// room (`up`), or from this office's.
    fn voice_to(&self, key: &str, up: bool, head: &Bytes) {
        if let Some(v) = self.visits.values().find(|v| v.up && !v.local && v.peer.get("key").and_then(Value::as_str) == Some(key)) {
            let _ = v.cmd.try_send(LinkCmd::Voice { up, data: head.clone() });
        }
    }

    /// The channel of a link up with the office with this key: one with a call on, if there is one (a packet then
    /// goes once, for both).
    fn link_with(&self, key: &str) -> Option<u16> {
        let links = || self.visits.values().filter(move |v| v.up && !v.local && v.peer.get("key").and_then(Value::as_str) == Some(key));
        links().find(|v| v.call_on).or_else(|| links().next()).map(|v| v.channel)
    }

    /// Whether this office's user hears the user of the office with this key in person (8.7): within VOICE_RANGE in
    /// the room they are in (a visitor near them at home; in another's room, as its owner says), or across a plot's
    /// side, near in both worlds. Then a call or a room voice with them goes quiet.
    fn in_person(&self, key: &str) -> bool {
        if key.is_empty() || self.voice_banned(key) {
            return false;
        }
        if let Some(v) = self.visiting() {
            return v.near_keys.iter().any(|k| k == key);
        }
        let Some(me) = self.voice.me else { return false };
        self.visits.values().any(|v| v.up && v.role == Role::Host && v.inside && v.peer.get("key").and_then(Value::as_str) == Some(key)
            && self.voice.visitors.get(&v.channel).is_some_and(|p| flat(*p, me) <= VOICE_RANGE))
            || (self.seam(key) && self.voice.world.get(key).is_some_and(|p| flat(*p, me) <= VOICE_RANGE))
    }

    /// Near each other across a plot's side (8.7): both at home, each within reach of the other in its own world.
    fn seam(&self, key: &str) -> bool {
        self.voice.world_told.contains(key) && self.voice.world_heard.contains(key)
    }

    fn on_call(&self, key: &str) -> bool {
        self.visits.values().any(|v| v.up && v.call_on && v.peer.get("key").and_then(Value::as_str) == Some(key))
    }

    /// Kept out of this office's room's voice, or its chat (8.9).
    fn voice_banned(&self, key: &str) -> bool {
        self.saved.bans.iter().any(|b| b.voice && b.key == key)
    }

    fn chat_banned(&self, key: &str) -> bool {
        self.saved.bans.iter().any(|b| b.chat && b.key == key)
    }

    /// Who this office's world has within reach of its user, both at home (8.7: across a plot's side): each told when
    /// that changes, over a link with them (and again over each that comes up).
    fn world_near(&mut self) {
        let me = if self.visiting().is_some() { None } else { self.voice.me };
        let now: HashSet<String> = me.map(|me| self.voice.world.iter().filter(|(_, p)| flat(**p, me) <= VOICE_RANGE + VOICE_MARGIN).map(|(k, _)| k.clone()).collect())
            .unwrap_or_default();
        for k in now.difference(&self.voice.world_told) {
            self.to_peer(k, json!({ "world_near": true }));
        }
        for k in self.voice.world_told.difference(&now) {
            self.to_peer(k, json!({ "world_near": false }));
        }
        self.voice.world_told = now;
    }

    /// Into a room voice (8.7), out of the one this office's user was in: a linked office's room's (it says who is in
    /// it), their own (everyone in it told), or none.
    fn voice_join(&mut self, key: Option<String>) {
        let old = self.room_voice.clone();
        self.voice_join_now(key.clone());
        let Some(old) = old.filter(|o| key.as_deref() != Some(o.as_str())) else { return };
        if old == self.key {   // 4.0: out of this office's own room's voice: its huddle, if one, is over
            if let Some(g) = self.huddle.clone() {
                self.to_lobby(ToLobby::GroupHuddle { group: Some(g.clone()), on: false });
                self.huddle_over(&g);
            }
        } else {   // out of a huddle joined: its voice visit ends
            let joined: Vec<String> = self.visits.values()
                .filter(|v| v.voice && v.role == Role::Visitor && v.huddle.is_some() && v.peer.get("key").and_then(Value::as_str) == Some(old.as_str()))
                .map(|v| v.id.clone()).collect();
            for id in joined {
                self.to_lobby(end(&id, None));
            }
        }
    }

    fn voice_join_now(&mut self, key: Option<String>) {
        if self.room_voice == key {
            return;
        }
        match self.room_voice.take() {
            Some(old) if old == self.key => self.tell_voice_members(),
            Some(old) => {
                self.to_peer(&old, json!({ "voice_join": false }));
            }
            None => {}
        }
        self.room_voice = key.clone();
        match key {
            Some(k) if k == self.key => self.tell_voice_members(),
            Some(k) => {
                self.to_peer(&k, json!({ "voice_join": true }));
            }
            None => self.to_page(json!({ "type": "room_voice", "key": null })),
        }
    }

    /// Who is in this office's room's voice (8.7): its own user (if they are), and each member; told to each member,
    /// and to the page (`voice_members`; and `room_voice`, if its user is in it).
    fn tell_voice_members(&mut self) {
        let list = self.voice_members_json();
        for k in self.voice_members.keys() {
            self.to_peer(k, json!({ "voice_members": list }));
        }
        self.to_page(json!({ "type": "voice_members", "members": list }));
        if self.room_voice.as_deref() == Some(self.key.as_str()) {
            self.to_page(json!({ "type": "room_voice", "key": self.key, "name": self.office.get("name"), "members": list }));
        }
    }

    fn voice_members_json(&self) -> Value {
        let mut list: Vec<Value> = Vec::new();
        if self.room_voice.as_deref() == Some(self.key.as_str()) {
            list.push(json!({ "key": self.key, "name": self.my_name() }));
        }
        list.extend(self.voice_members.iter().map(|(k, n)| json!({ "key": k, "name": n })));
        json!(list)
    }

    /// The room voice this office's user is in, for a page that connects: its room (who is in it comes again).
    fn room_voice_json(&self) -> Value {
        match &self.room_voice {
            None => Value::Null,
            Some(k) if *k == self.key => json!({ "key": k, "name": self.office.get("name"), "members": self.voice_members_json() }),
            Some(k) => {
                let name = self.visits.values().find(|v| v.peer.get("key").and_then(Value::as_str) == Some(k.as_str())).and_then(|v| v.peer.get("name").cloned());
                json!({ "key": k, "name": name, "members": null })
            }
        }
    }

    fn banned_by_json(&self) -> Value {
        json!(self.banned_by.iter().map(|(k, (name, voice, chat))| json!({ "key": k, "name": name, "voice": voice, "chat": chat })).collect::<Vec<_>>())
    }

    fn voice_to_page(&self, ch: u16, route: u8, data: &[u8], relayed: bool) {
        if let Some(p) = &self.user_page {   // a page behind loses some: late is lost
            let _ = p.frames.try_send(if relayed { frames::relay_to_page(ch, route, data) } else { frames::audio_to_page(ch, route, data) });
        }
    }

    fn relay_to(&self, channels: &[u16], head: Bytes) {
        for v in self.visits.values().filter(|v| v.up && channels.contains(&v.channel)) {
            let _ = v.cmd.try_send(LinkCmd::Relay(head.clone()));
        }
    }

    /// The channels of this room's visitors within `reach` metres of a spot (as the room has them), but one.
    fn within(&self, at: [f64; 3], reach: f64, but: Option<u16>) -> Vec<u16> {
        self.visits.values().filter(|v| v.up && v.role == Role::Host && v.inside && !v.local && Some(v.channel) != but && !self.voice_banned(v.peer.get("key").and_then(Value::as_str).unwrap_or("")))
            .filter(|v| self.voice.visitors.get(&v.channel).is_some_and(|p| flat(*p, at) <= reach))
            .map(|v| v.channel).collect()
    }

    /// Who is near whom in this room (within VOICE_RANGE), for each to know: this office's page (`voice_near`)
    /// and each visitor in it (`near`, over its link); with a call on speaker, its caller for those near the
    /// phone. Told when it changes; their distances, at most every NEAR_EVERY.
    fn tell_near(&mut self) {
        let me_entry = json!({ "key": self.key, "name": self.my_name() });
        let mut people: Vec<(u16, [f64; 3], Value)> = self.voice.me.map(|me| (0, me, me_entry)).into_iter().collect();
        for v in self.visits.values().filter(|v| v.up && v.role == Role::Host && v.inside && !v.local && !self.voice_banned(v.peer.get("key").and_then(Value::as_str).unwrap_or(""))) {
            if let Some(at) = self.voice.visitors.get(&v.channel) {   // (the owner is `me`, not a visitor near themselves)
                people.push((v.channel, *at, json!({ "key": v.peer.get("key"), "name": person(v), "id": v.id })));
            }
        }
        let caller = self.visits.values().find(|v| v.up && v.call_on && v.speaker).map(person);
        let mut lists: Vec<(u16, Vec<Value>)> = Vec::new();
        for (ch, at, _) in &people {
            let mut near: Vec<Value> = people.iter().filter(|(o, p, _)| o != ch && flat(*p, *at) <= VOICE_RANGE).map(|(_, p, e)| {
                let mut e = e.clone();
                e["distance"] = json!(flat(*p, *at).round());
                e
            }).collect();
            if let (Some(name), Some(phone), true) = (&caller, self.voice.phone, *ch != 0) {
                if flat(phone, *at) <= VOICE_RANGE {
                    near.push(json!({ "key": "phone", "name": name, "phone": true, "distance": flat(phone, *at).round() }));
                }
            }
            lists.push((*ch, near));
        }
        if let Some(me) = self.voice.me {   // across a plot's side (8.7): friends near in both worlds, for this office's page
            if let Some((_, near)) = lists.iter_mut().find(|(c, _)| *c == 0) {
                for (key, p) in self.voice.world.iter().filter(|(k, p)| self.seam(k) && flat(**p, me) <= VOICE_RANGE) {
                    let name = self.visits.values().find(|v| v.peer.get("key").and_then(Value::as_str) == Some(key.as_str())).map(person);
                    near.push(json!({ "key": key, "name": name, "distance": flat(*p, me).round(), "world": true }));
                }
            }
        }
        for ch in self.voice.told.keys().copied().collect::<Vec<_>>() {   // no longer in the room (or not said): nobody near
            if !lists.iter().any(|(c, _)| *c == ch) {
                lists.push((ch, Vec::new()));
            }
        }
        for (ch, near) in lists {
            let near = Value::Array(near);
            let who = |l: &Value| l.as_array().map(|a| a.iter().map(|e| (e["key"].clone(), e["id"].clone())).collect::<Vec<_>>()).unwrap_or_default();
            if let Some((was, at)) = self.voice.told.get(&ch) {
                if *was == near || (who(was) == who(&near) && at.elapsed() < NEAR_EVERY) {
                    continue;
                }
            }
            if ch == 0 {
                self.to_page(json!({ "type": "voice_near", "near": near }));
            } else if let Some(v) = self.visits.values().find(|v| v.up && v.channel == ch) {
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "near": near })));
            }
            if near.as_array().is_some_and(Vec::is_empty) && (ch != 0 && !self.voice.visitors.contains_key(&ch)) {
                self.voice.told.remove(&ch);   // gone: told once, then forgotten
            } else {
                self.voice.told.insert(ch, (near, Instant::now()));
            }
        }
    }

    // ---------- landscapes (8.10): what never changes, sent once, kept ----------

    /// This room's landscape, from its page: kept, and offered by its hash to every visit to this room.
    fn land_set(&mut self, gz: Bytes) {
        let hash = land::hash(&gz);
        if self.land.as_ref().is_some_and(|(h, _)| *h == hash) {
            return;
        }
        info!(hash = &hash[..12], len = gz.len(), "this room's landscape");
        for v in self.visits.values().filter(|v| v.up && v.role == Role::Host) {
            let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "landscape": hash })));
        }
        self.land = Some((hash, gz));
    }

    /// A host's landscape this office asked for: kept and given to the page if it is the one the host named (by
    /// its hash), else dropped.
    fn land_in(&mut self, id: &str, gz: Bytes) {
        let Some(v) = self.visits.get(id).filter(|v| v.role == Role::Visitor) else { return };
        let (ch, key, want) = (v.channel, v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), v.land_hash.clone());
        let hash = land::hash(&gz);
        if want.as_deref() != Some(hash.as_str()) {
            return warn!(visit = id, "a landscape other than the one its host named: dropped");
        }
        self.lands.put(&key, &hash, &gz);
        self.land_to_page(ch, &key, gz);
    }

    /// An office's landscape for the page, unpacked off the async threads (checked JSON), as text: never dropped,
    /// as a frame may be. On a visit's channel, or 0: a plot kept while its office is away.
    fn land_to_page(&self, ch: u16, key: &str, gz: Bytes) {
        let Some(p) = &self.user_page else { return };
        let (text, key) = (p.text.clone(), json!(key).to_string());
        tokio::task::spawn_blocking(move || match frames::unpack_scene(&gz).map(String::from_utf8) {
            Ok(Ok(json)) => { let _ = text.send(PageOut::Text(format!("{{\"type\":\"landscape\",\"channel\":{ch},\"key\":{key},\"json\":{json}}}"))); }
            _ => warn!("a landscape kept that is not what it should be"),
        });
    }

    /// That office's plot and landscape, let go (the page told when it had the plot).
    fn forget_plot(&mut self, key: &str) {
        if self.lands.forget(key) {
            self.to_page(json!({ "type": "plot_forgotten", "key": key }));
        }
    }

    // ---------- chat (8.9): a line said, to everyone who can see its speaker ----------

    /// A line this office's user says, to everyone who can see them: to the room they are in (another's: its owner's
    /// visit-client passes it on to everyone there; their own: to each visitor in it, and to each office that has it
    /// in its world or its corner).
    fn say_out(&mut self, line: Value) {
        if let Some(v) = self.visiting() {
            let mut ctl = json!({ "say": line["text"] });
            if !line["target"].is_null() {
                ctl["target"] = line["target"].clone();
            }
            attach(&mut ctl, line["media"].as_array().cloned().unwrap_or_default(), line["links"].as_array().cloned().unwrap_or_default());
            let _ = v.cmd.try_send(LinkCmd::Ctl(ctl));
            return;
        }
        let mut line = line;
        line["owner"] = json!(true);
        // 3.4.2 (#54): as its user's voice goes: to each visitor in the room within VOICE_RANGE of them; across a plot's
        // side, to each friend near them in both worlds (seam_said). A room that has not placed its user (voice_where):
        // as before, everyone in it and watching it.
        let me = self.voice.me;
        for v in self.visits.values().filter(|v| v.up && v.role == Role::Host && !v.local && !self.chat_banned(v.peer.get("key").and_then(Value::as_str).unwrap_or(""))) {
            if me.is_none_or(|me| v.inside && self.voice.visitors.get(&v.channel).is_none_or(|p| flat(*p, me) <= VOICE_RANGE)) {
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "said": line })));
            }
        }
        if let Some(me) = me {
            let seam: Vec<String> = self.voice.world.iter().filter(|(k, p)| self.seam(k) && !self.chat_banned(k) && flat(**p, me) <= VOICE_RANGE)
                .map(|(k, _)| k.clone()).collect();
            for k in seam {
                if let Some(v) = self.link_with(&k).and_then(|ch| self.visits.values().find(|v| v.up && v.channel == ch)) {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "seam_said": line })));
                }
            }
        }
    }

    /// A line meant for one of this room's figures (3.3, #48): to this office's room's page (the host's: on the engine too,
    /// open or not its user's), whoever said it, aloud (`whisper` false: everyone who can see them had it too) or to the
    /// figure alone. The room answers with say_as, aloud or (`to`) to them alone; whom its figures answer is its own.
    fn addressed(&mut self, figure: &str, from: Value, text: &str, whisper: bool) {
        let name = from["name"].clone();
        self.to_page(json!({ "type": "addressed", "figure": figure, "from": from, "name": name, "text": text, "whisper": whisper }));
    }

    /// A line a visitor in this room said: to this office's page, and to everyone else who can see this room (its other
    /// visitors, and those who have it in their worlds or corners).
    fn say_in(&mut self, from: u16, line: Value) {
        if self.chat_banned(line["key"].as_str().unwrap_or("")) {   // kept out of this room's chat
            return;
        }
        // 3.4.2 (#54): as its sayer's voice goes: to each other visitor within VOICE_RANGE of them (a visitor's line goes
        // no farther: not across the room's plot's side). One the room has not placed (voice_where): as before, to
        // everyone in it and watching it.
        let at = self.voice.visitors.get(&from).copied();
        for v in self.visits.values().filter(|v| v.up && v.role == Role::Host && !v.local && v.channel != from && !self.chat_banned(v.peer.get("key").and_then(Value::as_str).unwrap_or(""))) {
            if at.is_none_or(|at| v.inside && self.voice.visitors.get(&v.channel).is_none_or(|p| flat(*p, at) <= VOICE_RANGE)) {
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "said": line })));
            }
        }
        let mut chat = line;
        chat["type"] = json!("chat");
        chat["channel"] = json!(from);
        // Its own page has every line said in its room (the room's logic may want them); one its user is not near (more
        // than VOICE_RANGE off, or not in it) is marked `far`: no line in its chat, no bubble, not kept.
        if at.is_some_and(|at| self.voice.me.is_none_or(|me| flat(at, me) > VOICE_RANGE)) {
            chat["far"] = json!(true);
        }
        self.to_page(chat);
    }

    /// A line said in this office's room's chat (8.9: by its own user, or into it from a linked office): to every office
    /// linked with it, either way (in it, watching it, or seen from it), once each, but its sayer's. One in the room has
    /// it on its visit there.
    fn room_said(&self, line: &Value, sayer: Option<&str>) {
        let mut links: Vec<&Visit> = self.visits.values().filter(|v| v.up && !v.local).collect();
        links.sort_by_key(|v| match v.role {
            Role::Host if v.inside => 0,
            Role::Host => 1,
            Role::Visitor => 2,
        });
        let mut told = HashSet::new();
        for v in links {
            let Some(key) = v.peer.get("key").and_then(Value::as_str) else { continue };
            if Some(key) != sayer && !self.chat_banned(key) && told.insert(key) {
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "sim_said": line })));
            }
        }
    }

    /// A control message to the office with this key, over any link up with it; false if there is none.
    fn to_peer(&self, key: &str, ctl: Value) -> bool {
        match self.visits.values().find(|v| v.up && !v.local && v.peer.get("key").and_then(Value::as_str) == Some(key)) {
            Some(v) => v.cmd.try_send(LinkCmd::Ctl(ctl)).is_ok(),
            None => false,
        }
    }

    /// This room has a phone, or no longer: every link told (and each new one, while it has).
    fn set_phone(&mut self, on: bool) {
        if self.phone == on {
            return;
        }
        self.phone = on;
        for v in self.visits.values().filter(|v| v.up && !v.local) {   // (a room's phone is not for its owner to call)
            let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "phone": on })));
        }
    }

    fn tell_avatars(&mut self) {
        let avatars = self.saved.avatars_json();
        self.to_page(json!({ "type": "avatars", "default": avatars["default"], "by": avatars["by"] }));
    }

    fn tell_ignored(&mut self) {
        let ignored = self.saved.ignored_json();
        self.to_page(json!({ "type": "ignored", "ignored": ignored }));
    }

    fn tell_picks(&mut self) {
        let (door, picks) = (self.door(), self.saved.picks_json());
        self.to_page(json!({ "type": "picks", "door": door, "picks": picks }));
    }

    /// Its link to the lobby, so others can visit: only while its page hosts its scene.
    fn publish_link(&mut self) {
        if let (true, true, Some((endpoint, addr))) = (self.online, self.hosting.is_some(), self.link.clone()) {
            let _ = self.lobby.send(LobbyOut::Msg(ToLobby::Link { endpoint: Some(endpoint), addr: Some(addr) }));
        }
    }

    /// The page hosts its scene (of this three.js revision), or no longer: the office's link published or
    /// withdrawn (withdrawn, it cannot be visited or knock; visits under way go on).
    fn set_hosting(&mut self, three: Option<String>) {
        let was = self.hosting.is_some();
        self.hosting = three;
        match (was, self.hosting.is_some()) {
            (false, true) => {
                info!(three = self.hosting.as_deref().unwrap_or(""), "the page hosts its scene: in the lobby to be visited");
                self.publish_link();
                self.home_start(None);
            }
            (true, false) if self.online => {
                info!("no page hosting: withdrawn from visits until one does");
                let _ = self.lobby.send(LobbyOut::Msg(ToLobby::Link { endpoint: None, addr: None }));
            }
            _ => {}
        }
    }

    async fn lobby_msg(&mut self, m: Value) {
        if self.saved.away || !self.signed_in {
            return;   // the last words of a session it has left
        }
        let t = m.get("type").and_then(Value::as_str).unwrap_or("").to_string();
        match t.as_str() {
            "welcome" => {
                let resumed = m.get("resumed").and_then(Value::as_bool).unwrap_or(false);
                // The knocks still waiting on this office come again after the welcome.
                self.knocks.clear();
                self.watch_order.clear();   // what was waiting for an answer from the last connection gets none
                if !resumed {
                    self.admitted.clear();
                    self.end_all("gone");
                } else if let Some(live) = m.get("visits").and_then(Value::as_array) {
                    // Back after a drop: a visit that ended while this office was away is over (the lobby
                    // lists those it still has; one agreed meanwhile follows as a `visit`).
                    let live: Vec<&str> = live.iter().filter_map(Value::as_str).collect();
                    let over: Vec<String> = self.visits.values().filter(|v| !v.local && !live.contains(&v.id.as_str())).map(|v| v.id.clone()).collect();
                    for id in over {
                        self.end_visit(&id, "gone");
                    }
                }
                self.office = json!({ "id": m.get("id"), "name": m.get("name") });
                self.online = true;
                self.lobby_chat = m.get("chat") == Some(&json!(true));
                self.lobby_voice = m.get("voice") == Some(&json!(true));
                if !resumed {   // a new session: no huddle of this office's goes on (and those going on are told again)
                    self.huddle = None;
                    self.huddles.clear();
                }
                self.lobby_info = json!({ "title": m.get("title"), "motd": m.get("motd"), "admin": m.get("admin") == Some(&json!(true)) });
                // Each comes again, whole, after the welcome: the ones it had count meanwhile.
                let had = std::mem::take(&mut self.groups);
                self.groups_before.extend(had);
                self.groups_settle = Some(Instant::now() + GROUPS_SETTLE);
                if self.links.is_none() {
                    let relays = self.relays.take().unwrap_or_else(|| {
                        let urls: Vec<String> = m.get("relays").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(str::to_string).collect();
                        if urls.is_empty() { Relays::Default } else { Relays::Custom(urls) }
                    });
                    match Links::start(relays, self.saved.key.clone(), self.me.clone()).await {
                        Ok(l) => self.links = Some(l),
                        Err(e) => warn!("could not start the link endpoint: {e:#}"),
                    }
                }
                info!(office = %self.office, resumed, "in the lobby");
                self.publish_link();
                let (office, knocking) = (self.office.clone(), m.get("knocking").cloned().unwrap_or(json!([])));
                self.to_page(json!({ "type": "online", "office": office, "knocking": knocking, "lobby_chat": self.lobby_chat, "lobby_voice": self.lobby_voice, "lobby_info": self.lobby_info }));
            }
            // A world group of this office's, whole (../../LOBBY.md): its members may watch each other's plots.
            "group" => {
                let Some(name) = m.get("name").and_then(Value::as_str).map(str::to_ascii_lowercase) else { return };
                self.groups_before.remove(&name);
                self.groups.insert(name, m.clone());
                self.to_page(m);
                if self.groups_settle.is_none() {
                    self.watchers_still_allowed();
                }
                self.watch_picks();
            }
            "group_left" => {
                if let Some(name) = m.get("group").and_then(Value::as_str) {
                    self.groups.remove(&name.to_ascii_lowercase());
                    self.groups_before.remove(&name.to_ascii_lowercase());
                }
                self.to_page(m);
                self.watchers_still_allowed();
            }
            "group_chat" => {
                let from = m.get("from").cloned().unwrap_or(Value::Null);
                let s = |x: &Value, n: usize| x.as_str().map(|s| s.chars().filter(|c| !c.is_control()).take(n).collect::<String>());
                if let (Some(text), Some(id), Some(group)) = (m.get("text").and_then(Value::as_str).and_then(said), s(&from["id"], 40), s(&m["group"], 24)) {
                    let key = s(&from["key"], 100).filter(|k| state::key_ok(k));
                    let user = key.as_deref().and_then(|k| self.visits.values().find(|v| v.peer.get("key").and_then(Value::as_str) == Some(k) && v.peer_user.is_some())).map(person);
                    let name = user.unwrap_or_else(|| json!(s(&from["name"], 24).unwrap_or_default()));
                    let mine = key.as_deref() == Some(self.key.as_str());
                    self.to_page(json!({ "type": "chat", "scope": "group", "group": group, "id": id, "key": key, "name": name, "text": text, "mine": mine }));
                }
            }
            "lobby" => {
                self.lobby_view = Some(m.clone());
                self.to_page(m);
                self.watch_picks();
                self.deliver_held();   // someone a whisper was left for, in it now
            }
            // This office's own knocks to watch, and what became of them: never the page's.
            "knock_sent" if m.get("watch").and_then(Value::as_bool) == Some(true) => {
                self.watch_order.pop_front();
                if let (Some(kid), Some(to)) = (m.get("id").and_then(Value::as_str), m.get("to").and_then(Value::as_str)) {
                    self.watch_knocks.insert(kid.to_string(), to.to_string());
                }
            }
            "answered" if m.get("watch").and_then(Value::as_bool) == Some(true) => {
                let to = m.get("knock").and_then(Value::as_str).and_then(|k| self.watch_knocks.remove(k));
                if let Some(to) = to {
                    self.watch_pending.remove(&to);
                    if m.get("accept").and_then(Value::as_bool) != Some(true) {
                        if let Some(key) = self.office_key(&to) {
                            self.forget_plot(&key);   // their door shut to this office, or no longer a guest: not kept
                            self.watch_declined.insert(key, Instant::now());
                        }
                    }
                }
            }
            "error" if m.get("ref").and_then(Value::as_str) == Some("watch") => {
                // The oldest knock to watch the lobby has not answered: refused (not ready, full...). Not again for a while.
                if let Some(to) = self.watch_order.pop_front() {
                    debug!(code = ?m.get("code"), to, "a knock to watch refused by the lobby");
                    self.watch_pending.remove(&to);
                    if let Some(key) = self.office_key(&to) {
                        self.watch_declined.insert(key, Instant::now());
                    }
                }
            }
            "knock" if m.get("voice") == Some(&json!(true)) => self.voice_knock(m),
            "knock" if m.get("watch").and_then(Value::as_bool) == Some(true) => {
                // To watch: a guest through the open door is let in; anyone else declined. The page is never asked.
                if !self.let_guest_in(&m) {
                    if let Some(kid) = m.get("id").and_then(Value::as_str) {
                        let kid = kid.to_string();
                        self.to_lobby(ToLobby::Answer { knock: Some(kid), accept: Some(false), message: None });
                    }
                }
            }
            // 4.0 (#57): a knock to walk in, or to link (an older lobby's, from an office before 4.0): declined, the page never
            // asked. Worlds are the way in.
            "knock" => {
                if let Some(kid) = m.get("id").and_then(Value::as_str) {
                    let kid = kid.to_string();
                    self.to_lobby(ToLobby::Answer { knock: Some(kid), accept: Some(false), message: Some("Visit 4: meet in a world".into()) });
                }
            }
            // A line said to everyone in the lobby (8.9), checked as any line is, to the page.
            // A line said to everyone in the lobby, or whispered to this office's user (8.9): checked as any line is, to the page.
            "lobby_chat" | "whisper" => {
                let from = m.get("from").cloned().unwrap_or(Value::Null);
                let s = |x: &Value, n: usize| x.as_str().map(|s| s.chars().filter(|c| !c.is_control()).take(n).collect::<String>());
                if let (Some(text), Some(id)) = (m.get("text").and_then(Value::as_str).and_then(said), s(&from["id"], 40)) {
                    let key = s(&from["key"], 100).filter(|k| state::key_ok(k));
                    let scope = if t == "whisper" { "whisper" } else { "lobby" };
                    // By the name they go by, if a link with them says it (the lobby knows offices only)
                    let user = key.as_deref().and_then(|k| self.visits.values().find(|v| v.peer.get("key").and_then(Value::as_str) == Some(k) && v.peer_user.is_some())).map(person);
                    let name = user.unwrap_or_else(|| json!(s(&from["name"], 24).unwrap_or_default()));
                    self.to_page(json!({ "type": "chat", "scope": scope, "id": id, "key": key, "name": name, "text": text }));
                }
            }
            "knock_cancelled" => {
                if let Some(id) = m.get("id").and_then(Value::as_str) {
                    self.knocks.remove(id);
                }
                self.to_page(m);
            }
            // 4.0: a huddle in one of this office's worlds, started or over: to the page (which rings for it).
            "huddle" => {
                let s = |x: &Value, n: usize| x.as_str().map(|s| s.chars().filter(|c| !c.is_control()).take(n).collect::<String>());
                let from = m.get("from").cloned().unwrap_or(Value::Null);
                let (Some(group), Some(on)) = (s(&m["group"], 24), m.get("on").and_then(Value::as_bool)) else { return };
                let key = s(&from["key"], 100).filter(|k| state::key_ok(k));
                if key.as_deref() == Some(self.key.as_str()) && !on {   // this office's own, ended by the lobby (out of that world)
                    self.huddle_over(&group);
                }
                let since = m.get("since").and_then(Value::as_u64).unwrap_or(0);
                let h = json!({ "type": "huddle", "group": group, "on": on, "since": since,
                                "from": { "id": s(&from["id"], 40), "name": s(&from["name"], 24), "key": key },
                                "mine": key.as_deref() == Some(self.key.as_str()) });
                if on {
                    self.huddles.insert(group.to_ascii_lowercase(), h.clone());
                } else {
                    self.huddles.remove(&group.to_ascii_lowercase());
                }
                self.to_page(h);
            }
            "visit" => self.visit_agreed(&m),
            "visit_ended" => {
                let id = m.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                let reason = m.get("reason").and_then(Value::as_str).unwrap_or("gone").to_string();
                self.end_visit(&id, &reason);
            }
            // The rest of what a lobby says, for the page as it is. Only these: a page's own answers (a pass, history,
            // ready...) never come from a lobby, which could make them up (a lobby is anyone's to run).
            "knock_sent" | "answered" | "error" | "groups" | "lobby_bans" | "browser_pending" | "browsers" => self.to_page(m),
            // 3.2: a browser this office allowed waits to show the room's confirmation. Its QR code: the lobby's page
            // with it (`#verify=`), for that browser's camera to open in that browser; and its digits, to type there.
            "browser_confirm" => {
                let s = |k: &str| m.get(k).and_then(Value::as_str).map(str::to_string);
                let (Some(code), Some(key), Some(confirm), Some(url)) = (s("code"), s("key"), s("confirm").and_then(|c| lobby_proto::confirm_of(&c)), self.lobby_url.clone()) else {
                    return debug!("a browser_confirm not as it should be");
                };
                let host = lobby_proto::authority(&url);
                let page = format!("{}://{host}/#verify={confirm}", if url.starts_with("wss://") { "https" } else { "http" });
                let expires_in = m.get("expires_in").and_then(Value::as_u64).unwrap_or(lobby_proto::CONFIRM_TTL).min(lobby_proto::CONFIRM_TTL);
                self.to_page(json!({ "type": "browser_confirm", "code": code, "key": key, "confirm": confirm, "url": page, "qr": qr_svg(&page), "expires_in": expires_in }));
            }
            _ => debug!(t, "a lobby message no page takes: dropped"),
        }
    }

    /// A knock from a guest (3.3: someone in one of this office's worlds), with the door open and the room hosted:
    /// answered yes here, at once, without the page (which hears the visit start), to watch or to visit. The
    /// knocker's key is the lobby's word until the visit is agreed: then the visitor's link must be that key
    /// (visit_agreed), and the link makes it prove it.
    fn let_guest_in(&mut self, m: &Value) -> bool {
        let from = m.get("from");
        let (Some(kid), Some(fid), Some(fkey)) = (
            m.get("id").and_then(Value::as_str),
            from.and_then(|f| f.get("id")).and_then(Value::as_str),
            from.and_then(|f| f.get("key")).and_then(Value::as_str),
        ) else {
            return false;
        };
        if !self.saved.door_open || self.hosting.is_none() || !self.is_guest(fkey) {
            return false;
        }
        info!(knock = kid, from = fid, "a guest at the open door: let in");
        self.admitted.insert(fid.to_string(), (fkey.to_string(), Instant::now()));
        let kid = kid.to_string();
        self.to_lobby(ToLobby::Answer { knock: Some(kid), accept: Some(true), message: None });
        true
    }

    /// 4.0: a knock to talk on this office's door. To join this office's huddle: a member of that world let in here,
    /// the page not asked; anyone else declined. Else the page rings (nobody is let in to talk without its user), unless
    /// its user ignores them or keeps them out of their room's voice: declined, the page never told.
    fn voice_knock(&mut self, m: Value) {
        let (Some(kid), Some(fid), Some(fkey)) = (
            m.get("id").and_then(Value::as_str).map(str::to_string),
            m["from"]["id"].as_str().map(str::to_string),
            m["from"]["key"].as_str().filter(|k| state::key_ok(k)).map(str::to_string),
        ) else {
            return;
        };
        let refused = self.voice_banned(&fkey) || self.saved.ignores(&json!({ "key": fkey }));
        if let Some(g) = m.get("huddle").and_then(Value::as_str) {
            let ours = self.huddle.as_deref().is_some_and(|h| h.eq_ignore_ascii_case(g));
            let member = self.groups.get(&g.to_ascii_lowercase()).and_then(|x| x["members"].as_array())
                .is_some_and(|ms| ms.iter().any(|x| x["key"].as_str() == Some(fkey.as_str())));
            let accept = ours && member && !refused;
            if accept {
                info!(knock = kid, from = fid, "into this office's huddle in {g}: let in");
                self.voice_let.insert(fid, (fkey, Instant::now()));
            }
            return self.to_lobby(ToLobby::Answer { knock: Some(kid), accept: Some(accept), message: None });
        }
        if refused {
            return self.to_lobby(ToLobby::Answer { knock: Some(kid), accept: Some(false), message: None });
        }
        self.knocks.insert(kid, (m.clone(), Instant::now()));
        self.to_page(m);
    }

    /// 4.0: this office's huddle in `group` over (its user ended it, left their room's voice, or left the world): every
    /// voice visit that joined it ends, and its user is out of their room's voice.
    fn huddle_over(&mut self, group: &str) {
        if !self.huddle.as_deref().is_some_and(|h| h.eq_ignore_ascii_case(group)) {
            return;
        }
        self.huddle = None;
        let joined: Vec<String> = self.visits.values()
            .filter(|v| v.voice && v.role == Role::Host && v.huddle.as_deref().is_some_and(|h| h.eq_ignore_ascii_case(group)))
            .map(|v| v.id.clone()).collect();
        for id in joined {
            self.to_lobby(end(&id, Some("kicked")));
        }
        if self.room_voice.as_deref() == Some(self.key.as_str()) {
            self.voice_join(None);
        }
    }

    // ---------- visits ----------

    fn visit_agreed(&mut self, m: &Value) {
        let (Some(id), Some(ticket), Some(role)) = (
            m.get("id").and_then(Value::as_str),
            m.get("ticket").and_then(Value::as_str),
            m.get("role").and_then(Value::as_str),
        ) else {
            return warn!("a visit without an id, ticket or role");
        };
        if self.visits.contains_key(id) {
            return;   // told again after a resume: it is already under way
        }
        let Some(links) = self.links.clone() else {
            return self.to_lobby(end(id, Some("link_failed")));
        };
        let role = if role == "host" { Role::Host } else { Role::Visitor };
        let peer_link = m.get("peer_link").cloned().unwrap_or(Value::Null);
        let endpoint = peer_link.get("endpoint").and_then(Value::as_str).map(str::to_string);
        // The other office, with its key: the link proves it (the host admits only that endpoint; the visitor
        // dials that very one).
        let mut peer = m.get("peer").cloned().unwrap_or(Value::Null);
        if let (Some(p), Some(k)) = (peer.as_object_mut(), &endpoint) {
            p.insert("key".into(), json!(k));
        }
        let watch = m.get("watch").and_then(Value::as_bool) == Some(true);
        let link = watch && m.get("link").and_then(Value::as_bool) == Some(true);
        let voice = !watch && m.get("voice").and_then(Value::as_bool) == Some(true);
        if (!watch || link) && !voice {   // 4.0 (#57): a visit by a knock, or a link: none (this office asks for none, and lets none in)
            warn!(visit = id, "a visit by a knock, or a link: Visit 4 has none: ended");
            return self.to_lobby(end(id, Some(if role == Role::Host { "kicked" } else { "left" })));
        }
        let huddle = m.get("huddle").and_then(Value::as_str).filter(|_| voice).map(|g| g.chars().filter(|c| !c.is_control()).take(24).collect::<String>());
        if voice {
            // 4.0: a voice visit is a call at once: only one this office asked for (its knock to talk), or let in (with
            // the key it let in). Any other (a lobby that makes visits up) is ended before any voice goes.
            let pid = peer.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            let ok = match role {
                Role::Host => self.voice_let.remove(&pid).is_some_and(|(k, at)| at.elapsed() < VOICE_ASK && endpoint.as_deref() == Some(k.as_str())),
                Role::Visitor => self.voice_asked.remove(&pid).is_some_and(|at| at.elapsed() < VOICE_ASK),
            };
            if !ok {
                warn!(visit = id, "a voice visit this office did not ask for, nor let in: ended");
                return self.to_lobby(end(id, Some(if role == Role::Host { "kicked" } else { "left" })));
            }
        }
        if watch && role == Role::Visitor {
            self.watch_pending.remove(peer.get("id").and_then(Value::as_str).unwrap_or(""));
        }
        if !watch && !voice && role == Role::Visitor {
            // A visit to another room: out of any plot this office had stepped into (in one room at a time).
            let inside: Vec<(String, u16)> = self.visits.values().filter(|v| v.role == Role::Visitor && v.watch && v.inside).map(|v| (v.id.clone(), v.channel)).collect();
            for (other, ch) in inside {
                self.step_out(&other);
                self.to_page(json!({ "type": "enter_refused", "channel": ch, "reason": "moved" }));
            }
        }
        let mut guest = false;
        if role == Role::Host && !voice {
            let pid = peer.get("id").and_then(Value::as_str).unwrap_or("");
            if let Some((key, _)) = self.admitted.remove(pid) {
                if endpoint.as_deref() != Some(key.as_str()) {
                    warn!(visit = id, "a guest let in whose link is another key: ended");
                    return self.to_lobby(end(id, Some("kicked")));
                }
                guest = true;
            }
        }
        let cmd = match role {
            Role::Host => links.host(id, ticket, &peer_link),
            Role::Visitor => links.visitor(id, ticket, &peer_link),
        };
        match cmd {
            Ok(cmd) => {
                let channel = self.channel();
                info!(visit = id, ?role, channel, voice, "visit agreed");
                self.visits.insert(id.to_string(), Visit {
                    id: id.to_string(), channel, role, peer, local: false, up: false, cmd,
                    last_seq: 0, inputs: (Instant::now(), 0), want_key: false, host_state: None, ctl: (Instant::now(), 0), key_asked: None, resync_asked: None, resync_due: false,
                    points: (Instant::now(), 0), guest, watch, inside: !watch && !voice, shared_adds: false, link, in_world: false, avatar_asked: false, possessions_asked: Vec::new(), peer_possessions: json!([]), peer_phone: false,
                    call_on: voice && huddle.is_none(), speaker: false, ring: None, voice, huddle, performs: (Instant::now(), 0), performed: Value::Null, wants: (Instant::now(), 0), says: (Instant::now(), 0),
                    landscapes: false, land_sent: None, land_hash: None, near_keys: Vec::new(), peer_user: None,
                });
            }
            Err(e) => {
                warn!(visit = id, "cannot make its link: {e:#}");
                self.to_lobby(end(&id, Some("link_failed")));
            }
        }
    }

    fn channel(&mut self) -> u16 {
        loop {
            let ch = self.next_channel;
            self.next_channel = if ch == u16::MAX { 1 } else { ch + 1 };
            if !self.visits.values().any(|v| v.channel == ch) {
                return ch;
            }
        }
    }

    fn end_visit(&mut self, id: &str, reason: &str) {
        let Some(v) = self.visits.remove(id) else { return };
        let _ = v.cmd.try_send(LinkCmd::Close(reason.to_string()));
        if let Some(links) = &self.links {
            links.forget(id);
        }
        info!(visit = id, reason, "visit ended");
        // Ended by its host on purpose (its door shut to this office, no longer a guest, kicked): its plot is not kept.
        if v.role == Role::Visitor && reason == "kicked" {
            if let Some(key) = v.peer.get("key").and_then(Value::as_str) {
                self.forget_plot(key);
            }
        }
        if v.role == Role::Host && v.inside {
            self.voice.visitors.remove(&v.channel);
            self.tell_near();
        }
        if v.up {
            let mut ended = json!({ "type": "visit_ended", "id": if v.local { HOME_ID } else { id }, "channel": v.channel, "reason": reason });
            if v.voice {
                ended["voice"] = json!(true);
            }
            if v.local {
                ended[if v.role == Role::Host { "owner" } else { "home" }] = json!(true);
            }
            self.to_page(ended);
        }
        if v.local {
            return;   // (nothing between this office and itself to forget)
        }
        if v.role == Role::Visitor && v.inside {   // out of another's room: home, if the owner's visit is on
            self.home_enter();
        }
        // 4.0: a huddle joined, over (its starter ended it, or this office's user left it): out of its room's voice. On
        // the starter's side, whoever it carried is out of its voice (their own word may have gone on the link that closed).
        if v.voice && v.huddle.is_some() {
            let key = v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string();
            if v.role == Role::Visitor && self.room_voice.as_deref() == Some(key.as_str()) {
                self.voice_join(None);
            } else if v.role == Role::Host && self.voice_members.remove(&key).is_some() {
                self.tell_voice_members();
            }
        }
        // Their last link gone: where they had put this office is no longer told, nor known; nor what is between them
        // by voice (8.7) and chat (8.9).
        if let Some(key) = v.peer.get("key").and_then(Value::as_str) {
            if !self.visits.values().any(|o| o.peer.get("key").and_then(Value::as_str) == Some(key)) {
                if self.seen_in.remove(key).is_some() {
                    self.to_page(json!({ "type": "seen", "key": key, "name": v.peer.get("name"), "at": null }));
                }
                self.voice.world_heard.remove(key);
                self.voice.world_told.remove(key);
                if self.voice_members.remove(key).is_some() {
                    self.tell_voice_members();
                }
                if self.room_voice.as_deref() == Some(key) {
                    self.room_voice = None;
                    self.to_page(json!({ "type": "room_voice", "key": null, "out": { "key": key, "name": v.peer.get("name"), "why": "gone" } }));
                }
                if self.banned_by.remove(key).is_some() {
                    self.to_page(json!({ "type": "banned", "key": key, "name": v.peer.get("name"), "voice": false, "chat": false }));
                }
                self.tell_near();
            }
        }
    }

    /// Every visit the lobby agreed: a new session, away, or signed out. Not the owner's visit (engine/SPEC.md 4): it is
    /// between this office's own two pages, nothing of the lobby's, and goes with its pages alone.
    fn end_all(&mut self, reason: &str) {
        for id in self.visits.values().filter(|v| !v.local).map(|v| v.id.clone()).collect::<Vec<_>>() {
            self.end_visit(&id, reason);
        }
    }

    /// The visit whose room this office's user is in, elsewhere: another office's room. None at home (the owner's visit
    /// does not count: voice and chat at home go by the room's own word).
    fn visiting(&self) -> Option<&Visit> {
        self.visits.values().find(|v| v.role == Role::Visitor && v.up && v.inside && !v.local)
    }

    /// The visit whose room this office's user is in, their own included: where their input and points go.
    fn inside_visit(&self) -> Option<&Visit> {
        self.visits.values().find(|v| v.role == Role::Visitor && v.up && v.inside)
    }

    /// The name this office's user goes by (8.9): theirs, as their page said, else this office's.
    fn my_name(&self) -> Value {
        self.user_name.clone().map_or_else(|| self.office.get("name").cloned().unwrap_or(Value::Null), Value::from)
    }

    /// The names the users of offices linked with this one go by, for a page that connects (8.9).
    fn users_json(&self) -> Value {
        let mut seen = HashSet::new();
        json!(self.visits.values().filter(|v| v.up && v.peer_user.is_some())
            .filter_map(|v| v.peer.get("key").and_then(Value::as_str).filter(|k| seen.insert(k.to_string())).map(|k| json!({ "key": k, "name": v.peer_user })))
            .collect::<Vec<_>>())
    }

    /// The lobby's id of the office with this key, if it is in the lobby.
    fn office_id_of(&self, key: &str) -> Option<String> {
        self.lobby_view.as_ref()?.get("offices")?.as_array()?.iter()
            .find(|o| o.get("key").and_then(Value::as_str) == Some(key))?
            .get("id").and_then(Value::as_str).map(str::to_string)
    }

    fn office_key(&self, id: &str) -> Option<String> {
        self.lobby_view.as_ref()?.get("offices")?.as_array()?.iter()
            .find(|o| o.get("id").and_then(Value::as_str) == Some(id))?
            .get("key").and_then(Value::as_str).map(str::to_string)
    }

    /// The world on (the page draws its picked plots) or off (its watches end).
    fn set_world(&mut self, on: bool) {
        self.world = on;
        self.world_ask = None;
        if on {
            self.watch_declined.clear();   // asked anew: try everyone again
            self.watch_picks();
        } else {
            let watches: Vec<String> = self.visits.values().filter(|v| v.role == Role::Visitor && v.watch && !v.link).map(|v| v.id.clone()).collect();
            for id in watches {
                self.to_lobby(end(&id, None));
            }
        }
    }

    /// With the world on: a knock to watch each office its user picked (3.3), in the lobby, ready and its guest (in a
    /// world with it), that this office does not watch yet, unless one is waiting or was declined a while ago; at most
    /// MAX_WATCHING at once (its picks are no more).
    fn watch_picks(&mut self) {
        if !self.world || !self.online || self.hosting.is_none() || self.link.is_none() {
            return;
        }
        let now = Instant::now();
        self.watch_pending.retain(|_, at| now.duration_since(*at) < WATCH_PENDING);
        self.watch_declined.retain(|_, at| now.duration_since(*at) < WATCH_DECLINED);
        let me = self.office.get("id").and_then(Value::as_str).unwrap_or("").to_string();
        let watched: Vec<String> = self.visits.values().filter(|v| v.role == Role::Visitor && v.watch)
            .filter_map(|v| v.peer.get("id").and_then(Value::as_str).map(str::to_string)).collect();
        let mut room = MAX_WATCHING.saturating_sub(watched.len() + self.watch_pending.len());
        // Nobody's plot by itself: only those picked, in the order picked.
        let all: Vec<Value> = self.lobby_view.as_ref().and_then(|l| l.get("offices")).and_then(Value::as_array).cloned().unwrap_or_default();
        let offices: Vec<Value> = self.saved.picks.iter()
            .filter_map(|p| all.iter().find(|o| o.get("key").and_then(Value::as_str) == Some(p.key.as_str())).cloned()).collect();
        for o in offices {
            if room == 0 {
                break;
            }
            let (Some(id), Some(key)) = (o.get("id").and_then(Value::as_str), o.get("key").and_then(Value::as_str)) else { continue };
            // Only an office whose visit-client takes knocks to watch (an older one would show them to its page).
            if id == me || o.get("ready").and_then(Value::as_bool) != Some(true) || o.get("world").and_then(Value::as_bool) != Some(true)
                || watched.iter().any(|w| w == id)
                || self.watch_pending.contains_key(id) || self.watch_declined.contains_key(key) || !self.is_guest(key)
            {
                continue;
            }
            debug!(to = id, "a knock to watch a picked plot");
            self.watch_pending.insert(id.to_string(), now);
            self.watch_order.push_back(id.to_string());
            let _ = self.lobby.send(LobbyOut::Msg(ToLobby::Knock { to: Some(id.to_string()), message: None, watch: true, link: false, voice: false, huddle: None }));
            room -= 1;
        }
    }

    /// The watches of this office's plot by someone no longer its guest (in none of its worlds): ended.
    fn watchers_still_allowed(&mut self) {
        let allowed: HashSet<String> = self.co_members();
        self.end_watchers(|k| !allowed.contains(k));
    }

    /// The watches of this office's plot whose watcher's key passes `which`: ended (`kicked`). Not links: their
    /// host let them in itself.
    fn end_watchers(&mut self, which: impl Fn(&str) -> bool) {
        let ends: Vec<String> = self.visits.values()
            .filter(|v| v.role == Role::Host && v.watch && !v.link && which(v.peer.get("key").and_then(Value::as_str).unwrap_or("")))
            .map(|v| v.id.clone()).collect();
        for id in ends {
            self.to_lobby(end(&id, Some("kicked")));
        }
    }

    /// Out of a watched plot's room (visitor side): its host no longer has this office; the watch goes on.
    fn step_out(&mut self, id: &str) {
        if let Some(v) = self.visits.get_mut(id).filter(|v| v.inside) {
            v.inside = false;
            v.performed = Value::Null;   // (4.0: out of the room, nothing it had this office's user do)
            let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "exit": true })));
        }
    }

    /// Visitors' requests for the whole scene that may go to the page now (at most every RESYNC_EVERY each).
    fn resyncs(&mut self) {
        let due: Vec<u16> = self.visits.values_mut()
            .filter(|v| v.resync_due && v.resync_asked.is_none_or(|t| t.elapsed() >= RESYNC_EVERY))
            .map(|v| { v.resync_due = false; v.resync_asked = Some(Instant::now()); v.channel })
            .collect();
        for ch in due {
            self.to_page(json!({ "type": "scene_resync", "channel": ch }));
        }
    }

    /// A control message to every visitor of this office.
    fn tell_hosted(&self, ctl: Value) {
        for v in self.visits.values().filter(|v| v.role == Role::Host && v.up) {
            let _ = v.cmd.try_send(LinkCmd::Ctl(ctl.clone()));
        }
    }

    fn link_up(&mut self, id: &str, _first: bool) {
        self.link_up_quiet(id, false);
    }

    /// A link up (or back after a blip): the other side told what it needs to know (this room's phone and landscape,
    /// where this world has it, bans, the name this office's user goes by, what they own), and the page told it started,
    /// unless `quiet` (a page that hears of it in its ready).
    fn link_up_quiet(&mut self, id: &str, quiet: bool) {
        let (has_page, phone) = (self.host_page.is_some(), self.phone);
        let Some(v) = self.visits.get_mut(id) else { return };
        let first = !v.up;
        v.up = true;
        let (role, cmd, local) = (v.role, v.cmd.clone(), v.local);
        if v.voice {   // 4.0: voice alone: who they are talking with, by name; into the huddle's room voice, if one
            let (key, huddle) = (v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), v.huddle.is_some());
            let started = started(v);
            if let Some(b) = self.saved.bans.iter().find(|b| b.key == key) {
                let _ = cmd.try_send(LinkCmd::Ctl(json!({ "banned": { "voice": b.voice, "chat": b.chat } })));
            }
            if let Some(name) = &self.user_name {
                let _ = cmd.try_send(LinkCmd::Ctl(json!({ "user": { "name": name } })));
            }
            if first && !quiet {
                info!(visit = id, "voice link up");
                self.to_page(started);
            }
            if huddle && role == Role::Visitor && self.room_voice.as_deref() != Some(key.as_str()) {
                self.voice_join(Some(key));
            }
            return;
        }
        if phone && !local {   // this room has a phone: the other side hears of it, as every link does
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "phone": true })));
        }
        if let Some((hash, _)) = self.land.as_ref().filter(|_| role == Role::Host) {   // this room's landscape, by its hash
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "landscape": hash })));
        }
        if let Some(at) = v.peer.get("key").and_then(Value::as_str).and_then(|k| self.seen_out.get(k)) {   // where they are in this world
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "seen": at })));
        }
        let key = v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string();
        if self.voice.world_told.contains(&key) {   // near in this world, both at home (8.7)
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "world_near": true })));
        }
        if let Some(b) = self.saved.bans.iter().find(|b| b.key == key) {   // kept out of this room's voice or chat (8.9)
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "banned": { "voice": b.voice, "chat": b.chat } })));
        }
        if let Some(name) = &self.user_name {   // the name this office's user goes by (8.9)
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "user": { "name": name } })));
        }
        if self.room_voice.as_deref() == Some(key.as_str()) && !local {   // in its room's voice: still
            let _ = cmd.try_send(LinkCmd::Ctl(json!({ "voice_join": true })));
        }
        let started = started(v);
        match role {
            Role::Visitor => {
                let _ = cmd.try_send(LinkCmd::Ctl(json!({ "shared_adds": true })));
                if !self.possessions.is_empty() {   // what this office's user owns (8.11)
                    let _ = cmd.try_send(LinkCmd::Ctl(json!({ "possessions": self.possessions.iter().map(|(p, _)| p.clone()).collect::<Vec<_>>() })));
                }
                let _ = cmd.try_send(LinkCmd::Ctl(json!({ "landscapes": true })));
                if !first {   // back after a blip: where everything is now
                    let _ = cmd.try_send(LinkCmd::Ctl(json!({ "keyframe": true })));
                }
            }
            // No page to render for it yet: the visitor's view waits (it hears host_resumed when one comes).
            Role::Host if !has_page => {
                let _ = cmd.try_send(LinkCmd::Ctl(json!({ "paused": true })));
            }
            Role::Host => {}
        }
        if first && !quiet {
            info!(visit = id, "link up");
            self.to_page(started);
        } else if !first {
            info!(visit = id, "link back");
        }
    }

    fn link_ctl(&mut self, id: &str, m: Value) {
        let Some(v) = self.visits.get_mut(id) else { return };
        if v.ctl.0.elapsed() >= Duration::from_secs(1) {
            v.ctl = (Instant::now(), 0);
        }
        v.ctl.1 += 1;
        if v.ctl.1 > CTL_PER_SECOND {
            if v.ctl.1 == CTL_PER_SECOND + 1 {
                warn!(visit = id, "the other side sends too many control messages: dropping some");
            }
            return;
        }
        let ch = v.channel;
        // 4.0: a voice visit carries who is talking (their name, a ban) and a room's voice (a huddle's): nothing of a room.
        if v.voice && !VOICE_CTL.iter().any(|k| m.get(*k).is_some()) {
            return debug!(visit = id, "a control message a voice visit does not carry: dropped");
        }
        if let Some(on) = m.get("phone").and_then(Value::as_bool) {   // the other room has a phone now, or no longer
            v.peer_phone = on;
            return self.to_page(json!({ "type": "phone", "channel": ch, "on": on }));
        }
        if let Some(act) = m.get("call").and_then(Value::as_str).filter(|a| ["ring", "answer", "hangup"].contains(a)) {
            let act = act.to_string();
            if !call_state(v, &act, false) {   // (3.4.3: an answer to no ring of this office's: nothing, and not told)
                return;
            }
            self.to_page(json!({ "type": "call", "channel": ch, "act": act }));
            return self.tell_near();
        }
        if let Some(text) = m.get("sim_say").and_then(Value::as_str).map(said_text) {   // a line said into this room from a linked office (8.9)
            let (media, links) = media::attachments(&m);
            if (text.is_empty() && media.is_empty() && links.is_empty()) || !counted(&mut v.says) {
                return;
            }
            let (key, name) = (v.peer.get("key").cloned().unwrap_or(Value::Null), person(v));
            if self.chat_banned(key.as_str().unwrap_or("")) {   // kept out of this room's chat
                return;
            }
            let mut line = json!({ "key": key, "name": name, "text": text });
            attach(&mut line, media, links);
            self.want_media(&line, id);
            self.room_said(&line, key.as_str());
            let mut chat = line;
            chat["type"] = json!("chat");
            chat["scope"] = json!("sim");
            chat["home"] = json!(true);
            chat["channel"] = json!(ch);
            return self.to_page(chat);
        }
        if let Some(w) = m.get("whisper").filter(|w| w.is_object()) {   // whispered over a link, with pictures or links (8.12)
            let text = w.get("text").and_then(Value::as_str).map(said_text).unwrap_or_default();
            let (media, links) = media::attachments(w);
            if (text.is_empty() && media.is_empty() && links.is_empty()) || !counted(&mut v.says) {
                return;
            }
            let (key, name) = (v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), person(v));
            let mut line = json!({ "type": "chat", "scope": "whisper", "id": self.office_id_of(&key), "key": key, "name": name, "text": text });
            attach(&mut line, media, links);
            self.want_media(&line, id);
            return self.to_page(line);
        }
        if let Some(line) = m.get("seam_said").and_then(said_from) {   // 3.4.2 (#54): said by the other office's user, at home, near this one's across a plot's side
            if !counted(&mut v.says) {
                return;
            }
            let (key, name) = (v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), person(v));
            // Only if this side has them within VOICE_RANGE too (both at home, near in both worlds, each by its own
            // world's measure, as in_person has it), and does not keep them out of its chat.
            let near = self.voice.me.zip(self.voice.world.get(&key)).is_some_and(|(me, p)| flat(*p, me) <= VOICE_RANGE);
            if self.visiting().is_none() && near && self.seam(&key) && !self.chat_banned(&key) && line["text"].as_str().is_some_and(|t| !t.is_empty()) {
                // By the visit that shows their room here (its plot in this office's world first, else a link's
                // corner), as a line from a room seen from outside comes: over its owner's head there.
                let shows = |world: bool| self.visits.values().find(|x| x.up && x.role == Role::Visitor && x.watch && (x.in_world || !x.link) == world
                    && x.peer.get("key").and_then(Value::as_str) == Some(key.as_str())).map(|x| x.channel);
                let channel = shows(true).or_else(|| shows(false)).unwrap_or(ch);
                let conversation = format!("room:{key}");
                return self.to_page(json!({ "type": "chat", "key": key, "name": name, "text": line["text"], "owner": true, "seam": true, "channel": channel, "conversation": conversation }));
            }
            return;
        }
        if let Some(mid) = m.get("media_want").and_then(Value::as_str).filter(|i| media::id_ok(i)) {   // a picture this office passed on (8.12)
            if v.wants.0.elapsed() >= Duration::from_secs(60) {
                v.wants = (Instant::now(), 0);
            }
            v.wants.1 += 1;
            if v.wants.1 > MEDIA_WANTS {   // (3.4.3: each read is up to 8 MB)
                return debug!(visit = id, "asked for too many pictures this minute: not read");
            }
            let _ = self.db.send(DbCmd::GetMedia { id: mid.to_string(), to: MediaTo::Link(id.to_string()) });
            return;
        }
        if let Some(mid) = m.get("media_none").and_then(Value::as_str).filter(|i| media::id_ok(i)) {   // one the other side does not have
            if self.media_asked.get(mid).is_some_and(|a| a.1 == id) {
                self.media_gone(mid);
            }
            return;
        }
        if let Some(u) = m.get("user") {   // the name the other office's user goes by (8.9), or none now
            v.peer_user = u.get("name").and_then(Value::as_str).map(state::clip_name).map(|n| n.trim().to_string()).filter(|n| !n.is_empty());
            let (key, user) = (v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), person(v));
            if let Some(n) = self.voice_members.get_mut(&key) {
                *n = user.clone();
                self.tell_voice_members();
            }
            self.to_page(json!({ "type": "user", "key": key, "name": user }));
            return self.tell_near();
        }
        let (key, name, user) = (v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), v.peer.get("name").cloned().unwrap_or(Value::Null), person(v));
        if let Some(near) = m.get("world_near").and_then(Value::as_bool) {   // the other office has this one within reach in its world, both at home (8.7)
            if near { self.voice.world_heard.insert(key) } else { self.voice.world_heard.remove(&key) };
            return self.tell_near();
        }
        if let Some(on) = m.get("voice_join").and_then(Value::as_bool) {   // into this office's room's voice, or out of it (8.7)
            if on && self.voice_banned(&key) {
                self.to_peer(&key, json!({ "voice_out": "banned" }));
                return;
            }
            let changed = if on { self.voice_members.insert(key, user).is_none() } else { self.voice_members.remove(&key).is_some() };
            if changed || on {
                self.tell_voice_members();
            }
            return;
        }
        if let Some(list) = m.get("voice_members").and_then(Value::as_array) {   // who is in the room voice this office's user is in
            if self.room_voice.as_deref() == Some(key.as_str()) {
                let members: Vec<Value> = list.iter().take(64).filter_map(|e| {
                    Some(json!({ "key": e.get("key").and_then(Value::as_str).filter(|k| state::key_ok(k))?, "name": state::clip_name(e.get("name").and_then(Value::as_str).unwrap_or("")) }))
                }).collect();
                self.to_page(json!({ "type": "room_voice", "key": key, "name": name, "members": members }));
            }
            return;
        }
        if let Some(why) = m.get("voice_out").and_then(Value::as_str) {   // out of that room's voice: kicked, or kept out
            if self.room_voice.as_deref() == Some(key.as_str()) {
                self.room_voice = None;
                let why = if why == "banned" { "banned" } else { "kicked" };
                self.to_page(json!({ "type": "room_voice", "key": null, "out": { "key": key, "name": name, "why": why } }));
            }
            return;
        }
        if let Some(b) = m.get("banned") {   // that room keeps this office's user out of its voice, its chat, or neither now (8.9)
            let (voice, chat) = (b.get("voice") == Some(&json!(true)), b.get("chat") == Some(&json!(true)));
            if voice || chat { self.banned_by.insert(key.clone(), (name.clone(), voice, chat)); } else { self.banned_by.remove(&key); }
            if voice && self.room_voice.as_deref() == Some(key.as_str()) {
                self.room_voice = None;
                self.to_page(json!({ "type": "room_voice", "key": null, "out": { "key": key, "name": name, "why": "banned" } }));
            }
            return self.to_page(json!({ "type": "banned", "key": key, "name": name, "voice": voice, "chat": chat }));
        }
        if let Some(line) = m.get("sim_said").and_then(said_from) {   // a line said in the other office's room's chat (8.9)
            self.want_media(&line, id);
            let mut chat = line;
            chat["type"] = json!("chat");
            chat["scope"] = json!("sim");
            chat["channel"] = json!(ch);
            if let Some(o) = chat.as_object_mut() {
                o.remove("id");
                o.remove("owner");
            }
            return self.to_page(chat);
        }
        if let Some(at) = m.get("seen") {   // where the other office has put this one in its world (or nowhere): to the page
            if key.is_empty() {
                return;
            }
            let at = seen_from(at).unwrap_or(Value::Null);
            if at.is_null() {
                self.seen_in.remove(&key);
            } else {
                self.seen_in.insert(key.clone(), (name.clone(), at.clone()));
            }
            return self.to_page(json!({ "type": "seen", "key": key, "name": name, "at": at }));
        }
        let Some(v) = self.visits.get_mut(id) else { return };
        match v.role {
            // From a watcher: stepping into this room (where, in its terms), or out of it. In, it counts toward
            // max_visitors: one past it is refused, and stays outside.
            Role::Host if v.watch && m.get("enter").is_some() => {
                if v.inside {
                    return;
                }
                let pose = m["enter"].is_object().then(|| clamp_pose(&m["enter"]));
                let full = self.visits.values().filter(|x| x.role == Role::Host && x.inside && !x.local).count() >= self.max_visitors as usize;
                let v = self.visits.get_mut(id).expect("just found");
                if full {
                    let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "enter_refused": "full" })));
                } else {
                    v.inside = true;
                    let mut enter = json!({ "type": "enter", "channel": ch });
                    if let Some(pose) = pose {   // none: a link stepping in, where the room puts newcomers
                        enter["pose"] = pose;
                    }
                    self.to_page(enter);
                }
            }
            Role::Host if v.watch && m.get("exit").is_some() => {
                if v.inside {
                    v.inside = false;
                    self.to_page(json!({ "type": "exit", "channel": ch }));
                    self.voice.visitors.remove(&ch);
                    self.tell_near();
                }
            }
            // From the visitor.
            Role::Host => {
                if m.get("avatar_none") == Some(&json!(true)) {   // asked for its avatar, the visitor has none (8.6)
                    self.to_page(json!({ "type": "avatar_none", "channel": ch }));
                } else if let Some(list) = m.get("possessions") {   // what the visitor owns (8.11): checked, kept, to the page
                    if let Some(list) = possessions_from(Some(list)) {
                        v.peer_possessions = json!(list);
                        self.to_page(json!({ "type": "possessions", "channel": ch, "list": list }));
                    }
                } else if m.get("landscapes") == Some(&json!(true)) {   // the visitor reads landscapes (8.10): its page told, once
                    if !v.landscapes {
                        v.landscapes = true;
                        self.to_page(json!({ "type": "landscapes", "channel": ch }));
                    }
                } else if let Some(want) = m.get("landscape_want").and_then(Value::as_str) {   // this room's landscape, once a link
                    if let Some((hash, gz)) = self.land.as_ref().filter(|(h, _)| h == want) {
                        if v.land_sent.as_deref() != Some(hash.as_str()) {
                            info!(visit = %v.id, hash = &hash[..12], "this room's landscape sent");
                            v.land_sent = Some(hash.clone());
                            let _ = v.cmd.try_send(LinkCmd::Land(gz.clone()));
                        }
                    }
                } else if m.get("shared_adds") == Some(&json!(true)) {
                    if !v.shared_adds {
                        v.shared_adds = true;
                        self.to_page(json!({ "type": "shared_adds", "channel": ch }));
                    }
                } else if let Some(text) = m.get("say").filter(|_| v.inside).and_then(Value::as_str).map(said_text) {
                    let (media, links) = media::attachments(&m);
                    if (!text.is_empty() || !media.is_empty() || !links.is_empty()) && counted(&mut v.says) {
                        let mut line = json!({ "key": v.peer.get("key"), "name": person(v), "id": v.id, "text": text });
                        attach(&mut line, media, links);
                        let target = m.get("target").and_then(target_from);
                        if let Some(t) = &target {
                            line["target"] = t.clone();
                        }
                        let from = json!({ "id": v.id, "key": v.peer.get("key"), "name": person(v) });
                        let banned = self.chat_banned(line["key"].as_str().unwrap_or(""));
                        self.want_media(&line, id);
                        self.say_in(ch, line);
                        if let Some(t) = target.filter(|t| t["kind"] == "figure" && !banned) {
                            self.addressed(t["id"].as_str().unwrap_or(""), from, &text, false);
                        }
                    }
                } else if let Some(text) = m.get("tell").filter(|_| v.inside).and_then(Value::as_str).map(said_text) {
                    // 3.3 (#48): a visitor's whisper to one of this room's figures: to this room's page alone.
                    let figure = m.get("figure").and_then(Value::as_str).filter(|f| link::possession_id_ok(f)).map(str::to_string);
                    if let Some(f) = figure.filter(|_| !text.is_empty() && counted(&mut v.says)) {
                        let from = json!({ "id": v.id, "key": v.peer.get("key"), "name": person(v) });
                        if !self.chat_banned(from["key"].as_str().unwrap_or("")) {
                            self.addressed(&f, from, &text, true);
                        }
                    }
                } else if let Some(a) = m.get("act").filter(|_| v.inside) {
                    // 3.3 (#48): an action of one of this room's figures, from its card (an interaction type the room
                    // takes): to its page, which runs it as it would a point at the figure, by its own rules.
                    let (figure, action) = (a.get("figure").and_then(Value::as_str), a.get("action").and_then(Value::as_str));
                    if let (Some(f), Some(t)) = (figure.filter(|f| link::possession_id_ok(f)), action.filter(|t| link::possession_id_ok(t))) {
                        if v.points.0.elapsed() >= Duration::from_secs(1) {
                            v.points = (Instant::now(), 0);
                        }
                        if v.points.1 < POINTS_PER_SECOND {
                            v.points.1 += 1;
                            let m = json!({ "type": "act", "channel": ch, "figure": f, "action": t });
                            self.to_page(m);
                        }
                    }
                } else if m.get("scene_resync").is_some() {
                    v.resync_due = true;
                    self.resyncs();
                } else if m.get("keyframe").is_some() && v.key_asked.is_none_or(|t| t.elapsed() >= KEY_EVERY) {
                    v.key_asked = Some(Instant::now());
                    self.to_page(json!({ "type": "keyframe", "channel": ch }));
                } else if let Some(p) = m.get("point").filter(|_| v.inside) {
                    if v.points.0.elapsed() >= Duration::from_secs(1) {
                        v.points = (Instant::now(), 0);
                    }
                    if v.points.1 < POINTS_PER_SECOND {
                        v.points.1 += 1;
                        let mut p = clamp_point(p);
                        p["type"] = json!("point");
                        p["channel"] = json!(ch);
                        self.to_page(p);
                    }
                }
            }
            // From the host.
            Role::Visitor => {
                if let Some(id) = m.get("possession_want").and_then(Value::as_str).filter(|id| link::possession_id_ok(id)) {   // the host would draw it (8.11)
                    if !v.possessions_asked.iter().any(|a| a == id) && v.possessions_asked.len() < MAX_POSSESSIONS {
                        v.possessions_asked.push(id.to_string());
                    }
                    if let Some((_, Some(gz))) = self.possessions.iter().find(|(p, _)| p["id"] == id) {
                        let _ = v.cmd.try_send(LinkCmd::Possession(id.to_string(), gz.clone()));
                    }
                } else if m.get("avatar_want").is_some() {   // the host's page would draw this office as itself (8.6)
                    v.avatar_asked = true;
                    match self.avatar.clone() {
                        Some(gz) => { let _ = v.cmd.try_send(LinkCmd::Avatar(gz)); }
                        None => { let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "avatar_none": true }))); }   // (it goes once one is given)
                    }
                } else if let Some(b) = m.get("body") {   // how the host's room draws this office's user there (8.6)
                    let mut b = body_from(b);
                    b["type"] = json!("body");
                    b["channel"] = json!(ch);
                    self.to_page(b);
                } else if let Some(h) = m.get("landscape").and_then(Value::as_str).filter(|h| land::hash_ok(h)) {   // the host's landscape (8.10)
                    let (h, key, cmd) = (h.to_string(), v.peer.get("key").and_then(Value::as_str).unwrap_or("").to_string(), v.cmd.clone());
                    v.land_hash = Some(h.clone());
                    match self.lands.get(&key, &h) {
                        Some(gz) => self.land_to_page(ch, &key, gz),   // kept: nothing to send
                        None => { let _ = cmd.try_send(LinkCmd::Ctl(json!({ "landscape_want": h }))); }
                    }
                } else if let Some(p) = m.get("paused").and_then(Value::as_bool) {
                    self.to_page(json!({ "type": if p { "host_paused" } else { "host_resumed" }, "channel": ch }));
                } else if let Some(line) = m.get("said").and_then(said_from) {   // a line said in the host's room (8.9)
                    self.want_media(&line, id);
                    let mut line = line;
                    line["type"] = json!("chat");
                    line["channel"] = json!(ch);
                    if m.get("whisper") == Some(&json!(true)) && line.get("npc").is_some() {   // 3.3: one of its figures, to this office's user alone
                        line["scope"] = json!("whisper");
                        line["conversation"] = json!(format!("room:{}", line["key"].as_str().unwrap_or("")));
                        if let Some(o) = line.as_object_mut() {
                            o.remove("target");
                        }
                    }
                    self.to_page(line);
                } else if let Some(s) = m.get("sound").filter(|_| v.inside) {   // 3.4 (#36): a sound of the host's room, once, from a point in it
                    let id = s.get("id").and_then(Value::as_str).filter(|id| media::id_ok(id));
                    let at = s.get("at").and_then(Value::as_array).filter(|a| a.len() == 3 && a.iter().all(|x| x.as_f64().is_some_and(|x| x.is_finite() && x.abs() <= 1e5)));
                    if let (Some(id), Some(at)) = (id, at) {
                        let num = |k: &str, d: f64, lo: f64, hi: f64| s.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(d, |x| x.clamp(lo, hi));
                        let m = json!({ "type": "sound", "channel": ch, "id": id, "at": at, "volume": num("volume", 1.0, 0.0, 1.0), "ref": num("ref", 2.0, 0.1, 1000.0), "far": num("far", 30.0, 0.5, 10000.0) });
                        self.to_page(m);
                    }
                } else if let Some(near) = m.get("near").filter(|_| v.inside) {   // who is near this office's user in the host's room (8.7)
                    let near = near_from(near);
                    v.near_keys = near.as_array().into_iter().flatten().filter_map(|e| e["key"].as_str().map(str::to_string)).collect();
                    self.to_page(json!({ "type": "voice_near", "channel": ch, "near": near }));
                } else if let Some(reason) = m.get("enter_refused").filter(|_| v.watch && v.inside) {
                    v.inside = false;
                    v.performed = Value::Null;
                    let reason = reason.as_str().unwrap_or("refused").chars().take(40).collect::<String>();
                    self.to_page(json!({ "type": "enter_refused", "channel": ch, "reason": reason }));
                } else if let Some(p) = m.get("perform").filter(|_| v.inside) {   // 4.0 (#59): what the room has this office's user do, hold
                    let act = p.get("act").and_then(|a| a.get("clip").and_then(Value::as_str).filter(|c| ACTS.contains(c)).map(|clip| json!({
                        "clip": clip, "loop": a.get("loop") == Some(&json!(true)),
                        "secs": a.get("secs").and_then(Value::as_f64).filter(|s| *s > 0.0).map(|s| s.min(600.0)) }))).unwrap_or(Value::Null);
                    let hold = p.get("hold") == Some(&json!(true));
                    v.performed = if act.is_null() && !hold { Value::Null } else { json!({ "act": act, "hold": hold }) };
                    self.to_page(json!({ "type": "perform", "channel": ch, "act": act, "hold": hold }));
                }
            }
        }
    }

    /// A visitor's input, checked and clamped, at most 60 a second, newest only.
    fn link_input(&mut self, id: &str, m: Value) {
        let Some(v) = self.visits.get_mut(id).filter(|v| v.role == Role::Host && v.inside) else { return };
        let seq = m.get("seq").and_then(Value::as_u64).unwrap_or(0);
        if seq <= v.last_seq {
            return;
        }
        if v.inputs.0.elapsed() >= Duration::from_secs(1) {
            v.inputs = (Instant::now(), 0);
        }
        if v.inputs.1 >= INPUTS_PER_SECOND {
            return;
        }
        v.inputs.1 += 1;
        v.last_seq = seq;
        let mut input = clamp_input(&m);
        input["type"] = json!("input");
        input["channel"] = json!(v.channel);
        input["seq"] = json!(seq);
        self.to_page(input);
    }

    /// A delta of the scene of the room this office is visiting.
    fn link_frame(&mut self, id: &str, frame: Frame) {
        let Some(v) = self.visits.get_mut(id).filter(|v| v.role == Role::Visitor) else { return debug!(id, "a delta for no visit") };
        let Some(page) = &self.user_page else { return debug!(id, "a delta with no page to show it") };
        if v.want_key && !frame.key {
            return;
        }
        trace!(id, key = frame.key, ts = frame.ts, "delta to the page");
        // A page falling behind loses deltas (its own queue: never its messages), then waits for a key delta.
        if page.frames.try_send(frame.to_page(v.channel)).is_err() {
            if !v.want_key {
                v.want_key = true;
                let _ = v.cmd.try_send(LinkCmd::Ctl(json!({ "keyframe": true })));
            }
        } else if frame.key {
            v.want_key = false;
        }
    }
}

fn started(v: &Visit) -> Value {
    let mut m = json!({ "type": "visit_started", "id": v.id, "channel": v.channel, "role": if v.role == Role::Host { "host" } else { "visitor" }, "peer": v.peer });
    if v.voice {   // 4.0: voice alone (a call, or a huddle joined): nothing of a room
        m["voice"] = json!(true);
        if let Some(g) = &v.huddle {
            m["huddle"] = json!(g);
        }
        return m;
    }
    if let Some(state) = &v.host_state {
        m["state"] = state.clone();
    }
    if v.guest {
        m["guest"] = json!(true);
    }
    if v.shared_adds {
        m["shared_adds"] = json!(true);
    }
    if v.landscapes {
        m["landscapes"] = json!(true);
    }
    if v.peer_phone {
        m["phone"] = json!(true);
    }
    if v.peer_possessions.as_array().is_some_and(|l| !l.is_empty()) {
        m["possessions"] = v.peer_possessions.clone();
    }
    if v.link {
        m["link"] = json!(true);
        if v.in_world {
            m["where"] = json!("world");
        }
    }
    if v.watch {
        m["watch"] = json!(true);
        m["inside"] = json!(v.inside);
    }
    if !v.performed.is_null() && v.role == Role::Visitor {   // 4.0 (#59): what its host has this office's user do, hold
        m["perform"] = v.performed.clone();
    }
    if v.local {
        m[if v.role == Role::Host { "owner" } else { "home" }] = json!(true);
        m["id"] = json!(HOME_ID);   // one id to both pages
    }
    m
}

/// Which page a visit is for (../../engine/SPEC.md 3.4): the owner's visit, its own side's page; the person's own
/// visits, links and watches, the user's; a visit to this room, both (the room to serve it, the person to see it).
fn visit_for(v: &Visit) -> Option<PageRole> {
    if v.voice {   // 4.0: a voice visit is the person's, either way
        return Some(PageRole::User);
    }
    match (v.local, v.role) {
        (true, Role::Host) => Some(PageRole::Host),
        (true, Role::Visitor) => Some(PageRole::User),
        (false, Role::Visitor) => Some(PageRole::User),
        (false, Role::Host) => None,
    }
}

/// Which page a message is for (../../engine/SPEC.md 3.4), by its type: the room's, the person's, or both.
/// A URL as a QR code, an SVG (black on white, a quiet zone round it), for a phone's camera: the lobby's page, linked.
fn qr_svg(url: &str) -> Option<String> {
    let code = qrcode::QrCode::with_error_correction_level(url.as_bytes(), qrcode::EcLevel::L).ok()?;
    Some(code.render::<qrcode::render::svg::Color>().min_dimensions(240, 240).quiet_zone(true).build())
}

fn page_for(v: &Value) -> PageRole {
    match v.get("type").and_then(Value::as_str).unwrap_or("") {
        "visit_started" | "visit_ended" => {
            if v.get("owner") == Some(&json!(true)) { PageRole::Host }
            else if v.get("home") == Some(&json!(true)) || v.get("role") == Some(&json!("visitor")) || v.get("voice") == Some(&json!(true)) { PageRole::User }
            else { PageRole::Both }
        }
        "input" | "point" | "keyframe" | "scene_resync" | "shared_adds" | "landscapes" | "enter" | "exit" | "addressed" | "act"
        | "avatar" | "avatar_none" | "possessions" | "possession" => PageRole::Host,
        "chat" | "chat_log" | "unseen" | "held" | "held_sent" | "media_none" | "preview" | "history" | "shared"
        | "voice_near" | "room_voice" | "voice_members" | "user" | "banned" | "seen" | "layout" | "landscape"
        | "plot_forgotten" | "knock" | "knock_sent" | "knock_cancelled" | "answered" | "link_stats" | "scene"
        | "scene_lost" | "host_paused" | "host_resumed" | "enter_refused" | "body" | "call" | "phone" | "lobby_pass" | "browser_pending"
        | "browser_confirm" | "browsers" | "ignored" | "sound" | "huddle" | "perform" => PageRole::User,
        _ => PageRole::Both,
    }
}

/// Whether a page of this role may send a message of this type (../../engine/SPEC.md 3.3). A page without a role
/// may send them all.
fn role_may(role: PageRole, t: &str) -> bool {
    const HOST: &[&str] = &["host", "pose", "avatar_want", "possession_want", "phone", "body", "say_as", "sound_at", "perform"];
    const USER: &[&str] = &["input", "point", "keyframe", "scene_resync", "avatar_clear", "possessions", "user", "say", "attention", "read",
        "unhold", "media_get", "preview", "history", "shared", "voice_join", "call", "speaker", "world", "enter", "exit", "pick", "unpick", "place", "unplace", "ignore", "unignore", "ignore_clear", "act",
        "seen", "plot_keep", "plot_load", "plot_forget", "knock", "answer", "leave_lobby", "join_lobby", "status", "rename",
        "group_create", "group_join", "group_leave", "group_remove", "group_unban", "group_set", "group_owner", "group_delete", "groups", "admin",
        "lobby_pass", "browser_code", "browser_allow", "browsers", "browser_revoke", "huddle", "knock_cancel"];
    match role {
        PageRole::Both => true,
        PageRole::Host => !USER.contains(&t),
        PageRole::User => !HOST.contains(&t),
    }
}

/// Whether a page of this role may send a frame of this kind (5.4; ../../engine/SPEC.md 3.3): the room's scene, edits
/// and landscape are the host's; the person's avatar, possessions, voice and pictures are the user's.
fn frame_may(role: PageRole, b: &[u8]) -> bool {
    let Some(flags) = b.get(2) else { return true };   // (malformed: refused later, as such)
    let users = flags & (frames::AVATAR | frames::AUDIO | frames::POSSESSION) != 0;
    if flags & frames::MEDIA != 0 {
        return true;   // a picture or voice message (the user's), or a room's sound (3.4: its page's)
    }
    match role {
        PageRole::Both => true,
        PageRole::Host => !users,
        PageRole::User => users,
    }
}

/// Where a watcher steps into a room, in that room's own terms: x, y, z and yaw, each a number in range.
fn clamp_pose(m: &Value) -> Value {
    let num = |k: &str| m.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0.0, |x| x.clamp(-1e6, 1e6));
    let air = m.get("air").and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0.0, |x| x.clamp(0.0, 1e6));
    json!({ "x": num("x"), "y": num("y"), "z": num("z"), "yaw": num("yaw"), "air": air })
}

/// A point (8.3): where the visitor looked as it clicked, `yaw` and `pitch` in the same terms as its input, each
/// in its range, and for a turn of the wheel, `wheel`: whole notches, at most MAX_WHEEL either way (none: a click);
/// nothing else of what was sent passes.
fn clamp_point(m: &Value) -> Value {
    let angle = |k: &str, lim: f64| m.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0.0, |x| x.clamp(-lim, lim));
    let mut p = json!({ "yaw": angle("yaw", 1e6), "pitch": angle("pitch", 1.4) });
    let wheel = m.get("wheel").and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0, |x| x.round().clamp(-MAX_WHEEL, MAX_WHEEL) as i64);
    if wheel != 0 {
        p["wheel"] = json!(wheel);
    }
    p
}

/// A call's state on a link, by what passed either way (`mine`: this office's page did it; else the other side):
/// ringing when rung; on when the ring is answered by the side it rang (3.4.3: only then); over when hung up. Whether
/// it counted (an answer to no ring of the other side's is nothing).
fn call_state(v: &mut Visit, act: &str, mine: bool) -> bool {
    let Some((on, ring)) = call_next(v.call_on, v.ring, act, mine) else { return false };
    (v.call_on, v.ring) = (on, ring);
    if !on {
        v.speaker = false;
    }
    true
}

/// A call's state (on, ringing: rung by this side?) after `act`, by this side (`mine`) or the other; none if it is
/// nothing (an answer to no ring, or to one's own).
fn call_next(on: bool, ring: Option<bool>, act: &str, mine: bool) -> Option<(bool, Option<bool>)> {
    match act {
        "ring" if !on => Some((false, Some(mine))),
        "ring" => Some((on, ring)),
        "answer" if ring == Some(!mine) => Some((true, None)),
        "answer" => None,
        _ => Some((false, None)),   // hangup
    }
}

/// A line of chat as it may be said (8.9): no control characters, trimmed, at most SAY_LEN characters; none if empty.
fn said(text: &str) -> Option<String> {
    let t = said_text(text);
    (!t.is_empty()).then_some(t)
}

/// A line's text, checked: control characters as spaces, trimmed, at most SAY_LEN characters; empty if none (a
/// line may be pictures or links alone).
fn said_text(text: &str) -> String {
    let t: String = text.chars().map(|c| if c.is_control() { ' ' } else { c }).collect::<String>().trim().chars().take(SAY_LEN).collect();
    t.trim_end().to_string()
}

/// The name the other office's user goes by (8.9): theirs, if they said one (`user`), else their office's.
fn person(v: &Visit) -> Value {
    v.peer_user.clone().map_or_else(|| v.peer.get("name").cloned().unwrap_or(Value::Null), Value::from)
}

/// A line's pictures and links (8.12), set on it if it has any.
fn attach(line: &mut Value, media: Vec<Value>, links: Vec<Value>) {
    if !media.is_empty() {
        line["media"] = json!(media);
    }
    if !links.is_empty() {
        line["links"] = json!(links);
    }
}

/// The pictures a line names: its own, and its links' previews'.
fn media_ids(line: &Value) -> Vec<String> {
    let mut ids: Vec<String> = line["media"].as_array().into_iter().flatten().filter_map(|p| p["id"].as_str().map(str::to_string)).collect();
    ids.extend(line["links"].as_array().into_iter().flatten().filter_map(|l| l["image"]["id"].as_str().map(str::to_string)));
    ids
}

/// A line the host says was said in its room, checked: who (a key, a name; a visitor's visit id there, one of the
/// room's own figures, or the room's owner) and what.
fn said_from(v: &Value) -> Option<Value> {
    let s = |x: &Value, n: usize| x.as_str().map(|s| s.chars().filter(|c| !c.is_control()).take(n).collect::<String>());
    let text = said_text(v["text"].as_str().unwrap_or(""));
    let (media, links) = media::attachments(v);
    if text.is_empty() && media.is_empty() && links.is_empty() {
        return None;
    }
    let mut out = json!({ "key": s(&v["key"], 100)?, "name": s(&v["name"], 24).unwrap_or_default(), "text": text });
    attach(&mut out, media, links);
    if let Some(id) = s(&v["id"], 40) {
        out["id"] = json!(id);
    } else if let Some(npc) = v["npc"].as_str().filter(|id| link::possession_id_ok(id)) {
        out["npc"] = json!(npc);
    } else if v["owner"] == json!(true) {
        out["owner"] = json!(true);
    }
    if let Some(t) = v.get("target").and_then(target_from) {
        out["target"] = t;
    }
    Some(out)
}

/// What a line said aloud is meant for (3.3, #48): { kind: "figure", id: a room's figure's id } or { kind: "person", id:
/// an office's key (or another short id) }.
fn target_from(t: &Value) -> Option<Value> {
    let id = t.get("id").and_then(Value::as_str)?;
    match t.get("kind").and_then(Value::as_str)? {
        "figure" if link::possession_id_ok(id) => Some(json!({ "kind": "figure", "id": id })),
        "person" if !id.is_empty() && id.len() <= 100 && !id.chars().any(char::is_control) => Some(json!({ "kind": "person", "id": id })),
        _ => None,
    }
}

/// One more in a window of SAYS a SAY_WINDOW: true if it may go.
/// Whether a line's text mentions one of these names (8.9, as visit.js has it): @ and the name, as a word of its own
/// (after the start, a space, or an opening bracket or quote; before anything but a letter, a digit or _), in any case.
fn mentions(text: &str, names: &[Option<&str>]) -> bool {
    if !text.contains('@') {
        return false;
    }
    let t: Vec<char> = text.to_lowercase().chars().collect();
    names.iter().flatten().filter(|n| !n.is_empty()).any(|n| {
        let n: Vec<char> = n.to_lowercase().chars().collect();
        (0..t.len()).any(|i| {
            t[i] == '@' && (i == 0 || t[i - 1].is_whitespace() || "([{\"'".contains(t[i - 1])) && t[i + 1..].starts_with(&n)
                && t.get(i + 1 + n.len()).is_none_or(|c| !(c.is_alphanumeric() || *c == '_'))
        })
    })
}

/// A conversation's name (8.12): a room's (room:<its owner's key>), a person's (person:<their key>), or the lobby's.
fn conversation_ok(c: &str) -> bool {
    c.len() <= 120 && (c.starts_with("room:") || c.starts_with("person:") || c.starts_with("group:") || c == "lobby")
}

/// Ending a visit: `reason` none, the lobby's own (left for a visitor, kicked for a host).
fn end(visit: &str, reason: Option<&str>) -> ToLobby {
    ToLobby::End { visit: Some(visit.to_string()), reason: reason.map(str::to_string) }
}

/// A room's line for the directory, as its page gives it: text cut to 200 characters, plain; at most 5 tags, each
/// lower-case letters, digits and -, each once. None if it is not that.
fn about_of(v: &Value) -> Option<About> {
    let a: About = serde_json::from_value(v.clone()).ok()?;
    let mut tags: Vec<String> = Vec::new();
    for t in a.tags {
        if !lobby_proto::tag_ok(&t) {
            return None;
        }
        if !tags.contains(&t) {
            tags.push(t);
        }
    }
    (tags.len() <= 5).then(|| About { text: lobby_proto::plain(&a.text, 200), tags })
}

fn counted(w: &mut (Instant, u32)) -> bool {
    if w.0.elapsed() >= SAY_WINDOW {
        *w = (Instant::now(), 0);
    }
    w.1 += 1;
    w.1 <= SAYS
}

/// A spot in a room, in metres: [x, y, z], each finite and within 100 km; null or anything else, none.
fn spot(v: Option<&Value>) -> Option<[f64; 3]> {
    let a = v?.as_array().filter(|a| a.len() == 3)?;
    let n = |i: usize| a[i].as_f64().filter(|x| x.is_finite() && x.abs() <= 1e5);
    Some([n(0)?, n(1)?, n(2)?])
}

/// How far apart two spots are across the floor (height aside), in metres.
fn flat(a: [f64; 3], b: [f64; 3]) -> f64 {
    (a[0] - b[0]).hypot(a[2] - b[2])
}

/// Who a host says is near this office's user (8.7), checked: at most 64, each with a key, a name, a distance and
/// (a visitor) the id of its visit there, or (a call on the room's speaker) `phone`.
fn near_from(v: &Value) -> Value {
    let s = |x: &Value, n: usize| x.as_str().map(|s| s.chars().take(n).collect::<String>());
    Value::Array(v.as_array().into_iter().flatten().take(64).filter_map(|e| {
        let mut out = json!({ "key": s(&e["key"], 100)?, "name": s(&e["name"], 24).unwrap_or_default(),
            "distance": e["distance"].as_f64().filter(|d| d.is_finite() && *d >= 0.0 && *d <= 1e5)? });
        if let Some(id) = s(&e["id"], 40) {
            out["id"] = json!(id);
        }
        if e["phone"] == json!(true) {
            out["phone"] = json!(true);
        }
        Some(out)
    }).collect())
}

/// Where an office has put another's plot in its world, beside its own (8.4), as sent and passed on: `dx`, `dz`
/// metres from its own plot's middle (within 100 km), `turn` quarter turns left (0-3), and its own plot's `size`.
fn seen_from(v: &Value) -> Option<Value> {
    let n = |k: &str| v.get(k).and_then(Value::as_f64).filter(|x| x.is_finite() && x.abs() <= 1e5);
    let turn = v.get("turn").and_then(Value::as_u64).filter(|t| *t < 4)?;
    Some(json!({ "dx": n("dx")?, "dz": n("dz")?, "turn": turn, "size": state::size_from(v.get("size"))? }))
}

/// A three.js revision, as `THREE.REVISION` says it: digits.
fn revision(r: &str) -> bool {
    (1..=4).contains(&r.len()) && r.bytes().all(|b| b.is_ascii_digit())
}

/// An input with every field in its range (../SPEC.md section 8.2). `t` (the visitor page's clock when it
/// sent it, ms) passes through as a number, for the host to echo in its deltas.
/// How a room draws a visitor (8.6), checked: `look` "theirs" or "mine", `eyes` (scene units, 0 to 1000) or null,
/// `problem` (at most 200 characters, no control characters) or null.
fn body_from(m: &Value) -> Value {
    let eyes = m.get("eyes").and_then(Value::as_f64).filter(|x| x.is_finite() && *x > 0.0).map(|x| x.min(1000.0));
    let problem = m.get("problem").and_then(Value::as_str).map(|s| s.chars().filter(|c| !c.is_control()).take(200).collect::<String>());
    json!({ "look": if m.get("look").and_then(Value::as_str) == Some("theirs") { "theirs" } else { "mine" }, "eyes": eyes, "problem": problem })
}

/// The most possessions an office says it has (8.11).
const MAX_POSSESSIONS: usize = 8;

/// A list of possessions (8.11) as a page or a visitor says it, checked: at most MAX_POSSESSIONS, each { id (unique),
/// name, mount, fly, speed (0.25 to 5 times walking pace), lift (0 to 5 m) }. None if it is not one.
fn possessions_from(l: Option<&Value>) -> Option<Vec<Value>> {
    let l = l?.as_array().filter(|l| l.len() <= MAX_POSSESSIONS)?;
    let mut out: Vec<Value> = Vec::new();
    for p in l {
        let id = p.get("id").and_then(Value::as_str).filter(|id| link::possession_id_ok(id))?;
        if out.iter().any(|o| o["id"] == id) {
            return None;
        }
        let num = |k: &str, d: f64, lo: f64, hi: f64| p.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(d, |x| x.clamp(lo, hi));
        out.push(json!({
            "id": id, "name": state::clip_name(p.get("name").and_then(Value::as_str).unwrap_or(id)),
            "mount": p.get("mount") == Some(&json!(true)), "fly": p.get("fly") == Some(&json!(true)),
            "speed": num("speed", 1.0, 0.25, 5.0), "lift": num("lift", 0.0, 0.0, 5.0),
        }));
    }
    Some(out)
}

fn clamp_input(m: &Value) -> Value {
    let step = |k: &str| m.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0, |x| x.round().clamp(-1.0, 1.0) as i64);
    let angle = |k: &str, lim: f64| m.get(k).and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0.0, |x| x.clamp(-lim, lim));
    // What the visitor rides (8.11): one of its possessions; the host decides.
    let riding = m.get("riding").and_then(Value::as_str).filter(|r| link::possession_id_ok(r));
    json!({
        "forward": step("forward"), "strafe": step("strafe"), "up": step("up"), "run": m.get("run").and_then(Value::as_bool).unwrap_or(false),
        "riding": riding,
        "yaw": angle("yaw", 1e6), "pitch": angle("pitch", 1.4),
        "t": m.get("t").and_then(Value::as_f64).filter(|x| x.is_finite()).map_or(0.0, |x| x.clamp(0.0, 1e12)),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_call_is_on_only_when_its_ring_is_answered_by_the_side_it_rang() {
        // 3.4.3: the other side's answer to nothing (or to its own ring) turns nothing on.
        assert_eq!(call_next(false, None, "answer", false), None);
        assert_eq!(call_next(false, Some(false), "answer", false), None);
        assert_eq!(call_next(false, None, "answer", true), None);
        // This side rings, the other answers; the other rings, this side answers.
        assert_eq!(call_next(false, None, "ring", true), Some((false, Some(true))));
        assert_eq!(call_next(false, Some(true), "answer", false), Some((true, None)));
        assert_eq!(call_next(false, Some(false), "answer", true), Some((true, None)));
        // A ring while a call is on changes nothing; a hangup ends a call, or a ring, either way.
        assert_eq!(call_next(true, None, "ring", false), Some((true, None)));
        assert_eq!(call_next(true, None, "hangup", false), Some((false, None)));
        assert_eq!(call_next(false, Some(true), "hangup", true), Some((false, None)));
        // And once on, an answer again is nothing.
        assert_eq!(call_next(true, None, "answer", false), None);
    }

    #[test]
    fn a_rooms_figures_are_the_rooms_to_say() {
        assert!(role_may(PageRole::Host, "say_as") && role_may(PageRole::Both, "say_as") && !role_may(PageRole::User, "say_as"));
        assert!(!role_may(PageRole::Host, "say") && role_may(PageRole::User, "say"));
    }

    #[test]
    fn revisions_are_digits() {
        assert!(revision("128") && revision("186"));
        assert!(!revision("r128") && !revision("") && !revision("12345") && !revision("1.2"));
    }

    #[test]
    fn mentions_as_visit_js_has_them() {
        let me = [Some("Ann"), Some("Gooshy's room")];
        assert!(mentions("hey @ann!", &me) && mentions("@Ann", &me) && mentions("(@ANN) look", &me) && mentions("for @gooshy's room, all", &me));
        assert!(!mentions("hey @Anna", &me) && !mentions("mail@ann.test", &me) && !mentions("@ann_x", &me) && !mentions("Ann, hi", &me));
        assert!(!mentions("@ann", &[None, Some("")]));
    }

    #[test]
    fn a_line_said_is_plain_and_short() {
        assert_eq!(said("  hi\tthere\n "), Some("hi there".to_string()));
        assert_eq!(said(&"é".repeat(300)).map(|t| t.chars().count()), Some(SAY_LEN));
        assert!(said(" \n\t ").is_none() && said("").is_none());
        let line = said_from(&json!({ "key": "k1", "name": "Ada\u{7}", "id": "v1", "owner": true, "text": "hello\u{1b}[31m", "evil": 1 })).unwrap();
        assert_eq!(line, json!({ "key": "k1", "name": "Ada", "id": "v1", "text": "hello [31m" }));
        assert_eq!(said_from(&json!({ "key": "k2", "owner": true, "text": "yo" })), Some(json!({ "key": "k2", "name": "", "owner": true, "text": "yo" })));
        assert!(said_from(&json!({ "key": "k3", "text": "  " })).is_none() && said_from(&json!({ "text": "no key" })).is_none());
        // one of the room's own figures: its id kept (never also the owner's), a bad id dropped (then it is nobody's)
        assert_eq!(said_from(&json!({ "key": "k4", "name": "Reception", "npc": "receptionist", "owner": true, "text": "Hello!" })),
                   Some(json!({ "key": "k4", "name": "Reception", "npc": "receptionist", "text": "Hello!" })));
        assert_eq!(said_from(&json!({ "key": "k4", "npc": "../x", "text": "hi" })), Some(json!({ "key": "k4", "name": "", "text": "hi" })));
        let mut w = (Instant::now(), 0);
        assert_eq!((0..7).map(|_| counted(&mut w)).filter(|ok| *ok).count(), SAYS as usize);
    }

    #[test]
    fn where_voices_are_and_who_is_near() {
        assert_eq!(spot(Some(&json!([1, 1.6, -2.5]))), Some([1.0, 1.6, -2.5]));
        assert!(spot(Some(&json!([1, 2]))).is_none() && spot(Some(&json!([1e6, 0, 0]))).is_none() && spot(Some(&Value::Null)).is_none() && spot(None).is_none());
        assert_eq!(flat([0.0, 0.0, 0.0], [3.0, 9.0, 4.0]), 5.0);   // across the floor: height aside
        let near = near_from(&json!([{ "key": "k1", "name": "Ada", "distance": 12, "id": "v1", "evil": 1 },
            { "key": "phone", "name": "Bo", "distance": 2, "phone": true }, { "name": "no key", "distance": 1 }, { "key": "k3", "distance": -1 }]));
        assert_eq!(near, json!([{ "key": "k1", "name": "Ada", "distance": 12.0, "id": "v1" }, { "key": "phone", "name": "Bo", "distance": 2.0, "phone": true }]));
        assert_eq!(near_from(&json!("nobody")), json!([]));
    }

    #[test]
    fn where_one_is_seen_is_bare() {
        let s = seen_from(&json!({ "dx": 22.5, "dz": -4, "turn": 1, "size": [12, 9], "others": [{ "key": "x" }] }));
        assert_eq!(s, Some(json!({ "dx": 22.5, "dz": -4.0, "turn": 1, "size": [12.0, 9.0] })));
        assert!(seen_from(&json!({ "dx": 1, "dz": 1, "turn": 4, "size": [1, 1] })).is_none());
        assert!(seen_from(&json!({ "dx": 1e6, "dz": 1, "turn": 0, "size": [1, 1] })).is_none());
        assert!(seen_from(&json!({ "dx": 1, "dz": 1, "turn": 0 })).is_none());
    }

    #[test]
    fn inputs_are_clamped() {
        let i = clamp_input(&json!({ "forward": 5, "strafe": -0.6, "up": 0.7, "run": "yes", "yaw": 3.0, "pitch": -9 }));
        assert_eq!(i, json!({ "forward": 1, "strafe": -1, "up": 1, "run": false, "riding": null, "yaw": 3.0, "pitch": -1.4, "t": 0.0 }));
        let j = clamp_input(&json!({ "t": 1234.5 }));
        assert_eq!(j, json!({ "forward": 0, "strafe": 0, "up": 0, "run": false, "riding": null, "yaw": 0.0, "pitch": 0.0, "t": 1234.5 }));
        assert_eq!(clamp_input(&json!({ "riding": "my-dragon.2" }))["riding"], json!("my-dragon.2"));
        for bad in [json!("a b"), json!("visit:horse"), json!(5), json!("x".repeat(41))] {
            assert_eq!(clamp_input(&json!({ "riding": bad }))["riding"], Value::Null);
        }
    }

    #[test]
    fn bodies_are_checked() {
        assert_eq!(body_from(&json!({ "look": "theirs", "eyes": 1.5, "problem": null })), json!({ "look": "theirs", "eyes": 1.5, "problem": null }));
        assert_eq!(body_from(&json!({ "look": "x", "eyes": -2, "problem": "a\u{7}b".repeat(300) }))["look"], json!("mine"));
        assert_eq!(body_from(&json!({ "eyes": "high" }))["eyes"], Value::Null);
        assert_eq!(body_from(&json!({ "problem": "x".repeat(300) }))["problem"].as_str().unwrap().len(), 200);
    }

    #[test]
    fn possessions_are_checked() {
        let l = possessions_from(Some(&json!([{ "id": "dragon", "name": "Smaug\u{7}", "mount": true, "fly": true, "speed": 9, "lift": -1, "extra": 1 }, { "id": "cat" }]))).unwrap();
        assert_eq!(l, vec![json!({ "id": "dragon", "name": "Smaug", "mount": true, "fly": true, "speed": 5.0, "lift": 0.0 }),
            json!({ "id": "cat", "name": "cat", "mount": false, "fly": false, "speed": 1.0, "lift": 0.0 })]);
        assert!(possessions_from(Some(&json!([{ "id": "a" }, { "id": "a" }]))).is_none());   // twice the same
        assert!(possessions_from(Some(&json!([{ "id": "my:horse" }]))).is_none());   // not an id
        assert!(possessions_from(Some(&json!((0..9).map(|i| json!({ "id": format!("p{i}") })).collect::<Vec<_>>()))).is_none());
        assert!(possessions_from(Some(&json!("dragon"))).is_none() && possessions_from(None).is_none());
        assert_eq!(possessions_from(Some(&json!([]))), Some(vec![]));
    }

    #[test]
    fn an_entering_pose_says_how_high_in_the_air() {
        let p = clamp_pose(&json!({ "x": 1, "y": 2, "z": 3, "yaw": 0.5, "air": 7.5, "fly": true }));
        assert_eq!(p, json!({ "x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.5, "air": 7.5 }));
        assert_eq!(clamp_pose(&json!({ "air": -4 }))["air"], json!(0.0));
        assert_eq!(clamp_pose(&json!({ "x": 1 }))["air"], json!(0.0));
    }

    #[test]
    fn points_are_clamped_and_bare() {
        let p = clamp_point(&json!({ "type": "point", "yaw": 2.5, "pitch": 9, "target": "the front door", "channel": 3 }));
        assert_eq!(p, json!({ "yaw": 2.5, "pitch": 1.4 }));
        assert_eq!(clamp_point(&json!({ "yaw": "left", "pitch": null })), json!({ "yaw": 0.0, "pitch": 0.0 }));
    }

    #[test]
    fn a_points_wheel_is_whole_notches_in_range() {
        assert_eq!(clamp_point(&json!({ "yaw": 0, "pitch": 0, "wheel": 3 }))["wheel"], json!(3));
        assert_eq!(clamp_point(&json!({ "yaw": 0, "pitch": 0, "wheel": -2.6 }))["wheel"], json!(-3));
        assert_eq!(clamp_point(&json!({ "yaw": 0, "pitch": 0, "wheel": 500 }))["wheel"], json!(10));
        assert_eq!(clamp_point(&json!({ "yaw": 0, "pitch": 0, "wheel": -1e300 }))["wheel"], json!(-10));
        for w in [json!(0), json!(0.4), json!("down"), json!(null)] {
            assert_eq!(clamp_point(&json!({ "yaw": 0, "pitch": 0, "wheel": w })).get("wheel"), None, "a click: no wheel");
        }
    }
}
