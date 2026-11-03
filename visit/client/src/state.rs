//! What a visit-client keeps across its runs, in its state file (`--state-file`, ../SPEC.md section 4.1):
//! whether its office is out of the lobby, its link key (the office's lasting identity, and its secret half:
//! the file is readable by its owner only), its door, and its world (../WORLD.md): whose plots it shows (3.3) and
//! where it has put them; and whose look its visitors have in its room (their own avatars, or its models).
//! Written whole each time, by a rename. (Visit 0.7 to 0.9 kept agreed edges too, and up to 3.2 a list of guests:
//! a file that has them still loads.)
use std::{io::Write, path::Path};

use iroh::{EndpointId, SecretKey};
use serde_json::{json, Value};

/// The most people an office keeps apart (bans, looks); and plots it has placed.
pub const MAX_PEOPLE: usize = 256;
pub const MAX_PLACES: usize = 256;
/// The most plots an office's world shows at once (3.3: the ones its user picked; the lobby's own limit is the same).
pub const MAX_PICKS: usize = 8;

/// The most people (and rooms' figures) this office's user ignores (3.3, as WoW's ignore list: 50).
pub const MAX_IGNORED: usize = 50;

/// Someone this office's user ignores in chat (3.3): an office's key (a person); with `npc`, one of that office's room's
/// own figures (say_as) instead. Their lines never reach the user's page, and are not kept.
#[derive(Clone, Debug, PartialEq)]
pub struct Ignored {
    pub key: String,
    pub npc: Option<String>,
    pub name: String,
}

/// A room's figure's id (say_as): 1 to 40 letters, digits, `_`, `.` or `-`.
pub fn npc_ok(id: &str) -> bool {
    (1..=40).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-'))
}

/// Someone whose plot this office's user picked to show in its world (3.3): their key, and the name they were known by.
#[derive(Clone, Debug, PartialEq)]
pub struct Pick {
    pub key: String,
    pub name: String,
}

/// Someone this office's user keeps out of their room's voice, or its chat, or both (../SPEC.md 8.9): their key and
/// name. Kept until lifted.
#[derive(Clone, Debug, PartialEq)]
pub struct Ban {
    pub key: String,
    pub name: String,
    pub voice: bool,
    pub chat: bool,
}

/// Where this office has put a friend's plot in its world: its centre, and how many quarter turns it is turned.
#[derive(Clone, Debug, PartialEq)]
pub struct Place {
    pub key: String,
    pub x: f64,
    pub z: f64,
    pub turn: u8,
}

pub struct Saved {
    pub away: bool,
    pub key: SecretKey,
    pub door_open: bool,
    /// Whose plots its world shows (3.3), at most MAX_PICKS, in the order picked.
    pub picks: Vec<Pick>,
    /// Whom its user ignores in chat (3.3), at most MAX_IGNORED.
    pub ignored: Vec<Ignored>,
    pub layout: Vec<Place>,
    /// Whether visitors have their own avatars in this room by default (else its models; ../SPEC.md 8.6), and the
    /// keys for which it is the other way: (key, their own). Their own unless this office's user chose (`avatars_set`:
    /// a file from before Visit 0.16 has "mine", the default then, written whether chosen or not: their own now).
    pub avatars_theirs: bool,
    pub avatars_set: bool,
    pub avatars_by: Vec<(String, bool)>,
    /// Whom this office's room keeps out (../SPEC.md 8.9): kept in its database (Visit 0.20); a state file of the
    /// release before kept them here, and they move there once.
    pub bans: Vec<Ban>,
    /// The room's line in the lobby's list of who is here (../../LOBBY.md), told to the lobby.
    pub about: Option<lobby_proto::About>,
}

impl Saved {
    /// What the file says, and for anything it lacks (or no file at all) the defaults: in the lobby, a new
    /// key, the door open, no plots picked. With it, whether it lacked a key: then it should be written, to keep the
    /// one made now. A file from before 3.3 (no picks): the plots it had placed are its picks, by the names its guests
    /// had, if any; its guests are let go (3.3: a guest is whoever shares a world with it).
    pub fn load(path: Option<&Path>) -> (Saved, bool) {
        let v: Value = path.and_then(|p| std::fs::read_to_string(p).ok()).and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null);
        let key = v.get("key").and_then(Value::as_str).and_then(unhex32).map(|b| SecretKey::from_bytes(&b));
        let fresh = key.is_none();
        let list = |k: &str| v.get(k).and_then(Value::as_array).cloned().unwrap_or_default();
        let people = |k: &str| -> Vec<Pick> {
            list(k).iter().filter_map(|g| {
                let key = g.get("key").and_then(Value::as_str).filter(|k| key_ok(k))?;
                Some(Pick { key: key.to_string(), name: clip_name(g.get("name").and_then(Value::as_str).unwrap_or("")) })
            }).collect()
        };
        // Never this office's own room among the rooms round it (4.3.0): a file from before kept it if its Map ever
        // placed it, and 3.3 made every placed room a pick.
        let own = key.as_ref().map(|k| k.public().to_string());
        let mine = |k: &str| own.as_deref() == Some(k);
        let layout: Vec<Place> = list("layout").iter().filter_map(place_from).filter(|p| !mine(&p.key)).take(MAX_PLACES).collect();
        let mut picks: Vec<Pick> = Vec::new();
        let older = if v.get("picks").is_some() { people("picks") } else {
            let guests = people("guests");
            layout.iter().map(|p| Pick { key: p.key.clone(), name: guests.iter().find(|g| g.key == p.key).map(|g| g.name.clone()).unwrap_or_default() }).collect()
        };
        for p in older {
            if picks.len() < MAX_PICKS && !mine(&p.key) && !picks.iter().any(|x| x.key == p.key) {
                picks.push(p);
            }
        }
        let mut ignored: Vec<Ignored> = Vec::new();
        for i in list("ignored").iter().filter_map(ignored_from) {
            if ignored.len() < MAX_IGNORED && !ignored.iter().any(|x| x.key == i.key && x.npc == i.npc) {
                ignored.push(i);
            }
        }
        let av = v.get("avatars").cloned().unwrap_or(Value::Null);
        let avatars_set = av.get("set").and_then(Value::as_bool) == Some(true);
        let avatars_theirs = if avatars_set { use_from(av.get("default")).unwrap_or(true) } else { true };
        let avatars_by = av.get("by").and_then(Value::as_array).into_iter().flatten()
            .filter_map(|x| Some((x.get("key").and_then(Value::as_str).filter(|k| key_ok(k))?.to_string(), use_from(x.get("use"))?)))
            .filter(|(_, t)| *t != avatars_theirs)   // the same as the default: not apart
            .take(MAX_PEOPLE)
            .collect();
        let bans = list("bans").iter().filter_map(|b| {
            let (voice, chat) = (b.get("voice") == Some(&json!(true)), b.get("chat") == Some(&json!(true)));
            let key = b.get("key").and_then(Value::as_str).filter(|k| key_ok(k) && (voice || chat))?;
            Some(Ban { key: key.to_string(), name: clip_name(b.get("name").and_then(Value::as_str).unwrap_or("")), voice, chat })
        }).take(MAX_PEOPLE).collect();
        let saved = Saved {
            away: v.get("away").and_then(Value::as_bool).unwrap_or(false),
            key: key.unwrap_or_else(SecretKey::generate),
            door_open: v.get("door").and_then(Value::as_str) != Some("closed"),
            picks,
            ignored,
            layout,
            avatars_theirs,
            avatars_set,
            avatars_by,
            bans,
            about: v.get("about").filter(|a| a.is_object()).and_then(|a| serde_json::from_value(a.clone()).ok()),
        };
        (saved, fresh)
    }

    pub fn to_json(&self) -> Value {
        json!({
            "away": self.away, "key": hex(&self.key.to_bytes()), "door": if self.door_open { "open" } else { "closed" },
            "picks": self.picks_json(), "ignored": self.ignored_json(),
            "layout": self.layout_json(), "avatars": self.avatars_json(), "about": self.about,
        })
    }

    pub fn bans_json(&self) -> Value {
        json!(self.bans.iter().map(|b| json!({ "key": b.key, "name": b.name, "voice": b.voice, "chat": b.chat })).collect::<Vec<_>>())
    }

    pub fn avatars_json(&self) -> Value {
        let word = |theirs: bool| if theirs { "theirs" } else { "mine" };
        json!({ "default": word(self.avatars_theirs), "set": self.avatars_set, "by": self.avatars_by.iter().map(|(k, t)| json!({ "key": k, "use": word(*t) })).collect::<Vec<_>>() })
    }

    pub fn picks_json(&self) -> Value {
        json!(self.picks.iter().map(|g| json!({ "key": g.key, "name": g.name })).collect::<Vec<_>>())
    }

    pub fn ignored_json(&self) -> Value {
        json!(self.ignored.iter().map(|i| match &i.npc {
            Some(n) => json!({ "key": i.key, "npc": n, "name": i.name }),
            None => json!({ "key": i.key, "name": i.name }),
        }).collect::<Vec<_>>())
    }

    /// Whether a chat line is from someone ignored: by its sayer's key, and, a room's figure's, its id (8.9, `npc`).
    /// Never this office's user's own.
    pub fn ignores(&self, line: &Value) -> bool {
        if self.ignored.is_empty() || line.get("mine") == Some(&json!(true)) {
            return false;
        }
        let (Some(key), npc) = (line.get("key").and_then(Value::as_str), line.get("npc").and_then(Value::as_str)) else { return false };
        self.ignored.iter().any(|i| i.key == key && i.npc.as_deref() == npc)
    }

    pub fn layout_json(&self) -> Value {
        json!(self.layout.iter().map(|p| json!({ "key": p.key, "x": p.x, "z": p.z, "turn": p.turn })).collect::<Vec<_>>())
    }

    /// Written to a file beside it, readable by its owner only, then renamed over it.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        let tmp = path.with_extension("tmp");
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create(true).truncate(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut opts, 0o600);
        let mut f = opts.open(&tmp)?;
        f.write_all(self.to_json().to_string().as_bytes())?;
        f.sync_all()?;
        drop(f);
        std::fs::rename(&tmp, path)
    }
}

/// A place, as a page gives it or the file keeps it: a key, a centre (each within 100 km), 0-3 quarter turns.
pub fn place_from(v: &Value) -> Option<Place> {
    let key = v.get("key").and_then(Value::as_str).filter(|k| key_ok(k))?;
    let num = |k: &str| v.get(k).and_then(Value::as_f64).filter(|x| x.is_finite() && x.abs() <= 1e5);
    let turn = v.get("turn").and_then(Value::as_u64).unwrap_or(0);
    (turn < 4).then_some(())?;
    Some(Place { key: key.to_string(), x: num("x")?, z: num("z")?, turn: turn as u8 })
}

/// Someone to ignore, as a page gives it or the file keeps it: an office's key, a figure's id or none, a name.
pub fn ignored_from(v: &Value) -> Option<Ignored> {
    let key = v.get("key").and_then(Value::as_str).filter(|k| key_ok(k))?;
    let npc = match v.get("npc") {
        None | Some(Value::Null) => None,
        Some(n) => Some(n.as_str().filter(|n| npc_ok(n))?.to_string()),
    };
    Some(Ignored { key: key.to_string(), npc, name: clip_name(v.get("name").and_then(Value::as_str).unwrap_or("")) })
}

/// Whose look: "theirs" (true: a visitor's own avatar) or "mine" (false: this room's models).
pub fn use_from(v: Option<&Value>) -> Option<bool> {
    match v.and_then(Value::as_str) {
        Some("theirs") => Some(true),
        Some("mine") => Some(false),
        _ => None,
    }
}

/// A plot's size in metres: two numbers, each above 0 and at most 10 km.
pub fn size_from(v: Option<&Value>) -> Option<[f64; 2]> {
    let a = v?.as_array()?;
    let n = |i: usize| a.get(i).and_then(Value::as_f64).filter(|x| x.is_finite() && *x > 0.0 && *x <= 1e4);
    Some([n(0)?, n(1)?])
}

/// An office's key, as the lobby lists it: a link endpoint's public key.
pub fn key_ok(k: &str) -> bool {
    k.len() <= 100 && k.parse::<EndpointId>().is_ok()
}

/// A guest's name, as a page or the lobby gave it: at most 24 characters, none of them a control character.
pub fn clip_name(n: &str) -> String {
    n.chars().filter(|c| !c.is_control()).take(24).collect()
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn unhex32(s: &str) -> Option<[u8; 32]> {
    let b = s.as_bytes();
    if b.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    for (i, pair) in b.chunks(2).enumerate() {
        out[i] = u8::from_str_radix(std::str::from_utf8(pair).ok()?, 16).ok()?;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_kept_across_runs() {
        let dir = std::env::temp_dir().join(format!("visit-state-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        let _ = std::fs::remove_file(&path);
        let (first, fresh) = Saved::load(Some(&path));
        assert!(fresh && !first.away && first.door_open && first.picks.is_empty());
        let mut s = first;
        s.door_open = false;
        let friend = SecretKey::generate().public().to_string();
        s.picks.push(Pick { key: friend.clone(), name: "Ada".into() });
        s.layout.push(Place { key: friend, x: 20.5, z: -3.0, turn: 3 });
        s.save(&path).unwrap();
        let (again, fresh) = Saved::load(Some(&path));
        assert!(!fresh);
        assert_eq!((again.key.public(), again.door_open, again.picks.clone()), (s.key.public(), false, s.picks.clone()));
        assert_eq!(again.layout, s.layout);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_old_file_or_none_gets_the_defaults() {
        let (s, fresh) = Saved::load(None);
        assert!(fresh && !s.away && s.door_open);
        let dir = std::env::temp_dir().join(format!("visit-state-old-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        std::fs::write(&path, r#"{"away": true, "guests": [{"key": "not a key", "name": "x"}]}"#).unwrap();
        let (s, fresh) = Saved::load(Some(&path));
        assert!(fresh && s.away && s.picks.is_empty());
        // 3.3: a file from before picks: the plots it placed are picked (by its guests' names), at most MAX_PICKS;
        // its guests alone are not.
        let keys: Vec<String> = (0..10).map(|_| SecretKey::generate().public().to_string()).collect();
        let layout: Vec<Value> = keys[..9].iter().map(|k| json!({ "key": k, "x": 1.0, "z": 2.0, "turn": 0 })).collect();
        std::fs::write(&path, json!({ "guests": [{ "key": keys[0], "name": "Ada" }, { "key": keys[9], "name": "Bo" }], "layout": layout }).to_string()).unwrap();
        let (s, _) = Saved::load(Some(&path));
        assert_eq!(s.picks.iter().map(|p| p.key.clone()).collect::<Vec<_>>(), keys[..MAX_PICKS].to_vec());
        assert_eq!((s.picks[0].name.as_str(), s.picks[1].name.as_str(), s.layout.len()), ("Ada", "", 9));
        assert!(s.to_json().get("guests").is_none());
        // 4.3.0: its own room, picked or placed (a file from before), is neither; the others are kept.
        let me = SecretKey::generate();
        let own = me.public().to_string();
        std::fs::write(&path, json!({ "key": hex(&me.to_bytes()), "picks": [{ "key": own, "name": "" }, { "key": keys[1], "name": "Bo" }],
            "layout": [{ "key": own, "x": -78.5, "z": 9.0, "turn": 0 }, { "key": keys[1], "x": 1.0, "z": 2.0, "turn": 3 }] }).to_string()).unwrap();
        let (s, _) = Saved::load(Some(&path));
        assert_eq!((s.picks.iter().map(|p| p.key.clone()).collect::<Vec<_>>(), s.layout.iter().map(|p| p.key.clone()).collect::<Vec<_>>()),
                   (vec![keys[1].clone()], vec![keys[1].clone()]));
        std::fs::write(&path, json!({ "key": hex(&me.to_bytes()), "layout": [{ "key": own, "x": 1.0, "z": 2.0, "turn": 0 }] }).to_string()).unwrap();
        assert!(Saved::load(Some(&path)).0.picks.is_empty());   // (and 3.3's: a file from before picks)
        // 3.3: whom its user ignores: kept, a person by key, a room's figure by its key and id; at most MAX_IGNORED.
        let many: Vec<Value> = (0..60).map(|i| json!({ "key": keys[i % 10], "npc": format!("f{i}"), "name": "x" })).collect();
        std::fs::write(&path, json!({ "ignored": [{ "key": keys[0], "name": "Ada" }, { "key": keys[0], "name": "Ada again" }, { "key": "nope" }] }).to_string()).unwrap();
        let (s, _) = Saved::load(Some(&path));
        assert_eq!(s.ignored, vec![Ignored { key: keys[0].clone(), npc: None, name: "Ada".into() }]);
        assert!(s.ignores(&json!({ "key": keys[0], "text": "hi" })) && !s.ignores(&json!({ "key": keys[0], "npc": "guide", "text": "hi" })));
        assert!(!s.ignores(&json!({ "key": keys[0], "mine": true })) && !s.ignores(&json!({ "key": keys[1] })));
        std::fs::write(&path, json!({ "ignored": many }).to_string()).unwrap();
        let (s, _) = Saved::load(Some(&path));
        assert_eq!(s.ignored.len(), MAX_IGNORED);
        assert!(s.ignores(&json!({ "key": keys[3], "npc": "f3" })) && !s.ignores(&json!({ "key": keys[3] })));
        assert_eq!(Saved::load(Some(&path)).0.to_json()["ignored"].as_array().map(Vec::len), Some(MAX_IGNORED));
        // Visitors' own look by default; a file from before 0.16 says "mine" (its default then, never chosen): theirs
        // now, and a friend set apart as theirs is no longer apart. Chosen ("set"), it stays.
        assert!(Saved::load(None).0.avatars_theirs);
        let k = SecretKey::generate().public().to_string();
        std::fs::write(&path, json!({ "avatars": { "default": "mine", "by": [{ "key": k, "use": "theirs" }] } }).to_string()).unwrap();
        let (s, _) = Saved::load(Some(&path));
        assert!(s.avatars_theirs && !s.avatars_set && s.avatars_by.is_empty());
        std::fs::write(&path, json!({ "avatars": { "default": "mine", "set": true, "by": [{ "key": k, "use": "theirs" }] } }).to_string()).unwrap();
        let (s, _) = Saved::load(Some(&path));
        assert!(!s.avatars_theirs && s.avatars_set && s.avatars_by == vec![(k, true)]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_older_file_s_bans_are_read_not_written() {
        let dir = std::env::temp_dir().join(format!("visit-state-bans-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        let k = SecretKey::generate().public().to_string();
        // Read (to move into the database), and no longer written: the database keeps them.
        std::fs::write(&path, json!({ "bans": [{ "key": k, "name": "Ann", "voice": true }] }).to_string()).unwrap();
        let mut s = Saved::load(Some(&path)).0;
        assert_eq!(s.bans, vec![Ban { key: k.clone(), name: "Ann".into(), voice: true, chat: false }]);
        s.save(&path).unwrap();
        assert!(Saved::load(Some(&path)).0.bans.is_empty());
        // Not a key, or kept out of nothing: dropped. A name cut to 24 characters, without control characters.
        std::fs::write(&path, json!({ "bans": [{ "key": "nope", "voice": true }, { "key": k, "voice": false, "chat": false },
            { "key": k, "name": format!("a\u{7}{}", "b".repeat(40)), "chat": true }] }).to_string()).unwrap();
        let bans = Saved::load(Some(&path)).0.bans;
        assert_eq!(bans, vec![Ban { key: k, name: format!("a{}", "b".repeat(23)), voice: false, chat: true }]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn places_and_sizes_are_checked() {
        let k = SecretKey::generate().public().to_string();
        assert!(place_from(&json!({ "key": k, "x": 1, "z": 2, "turn": 1 })).is_some());
        assert!(place_from(&json!({ "key": k, "x": 1, "z": 2, "turn": 4 })).is_none());
        assert!(place_from(&json!({ "key": k, "x": 1e9, "z": 2 })).is_none());
        assert!(place_from(&json!({ "key": "nope", "x": 1, "z": 2 })).is_none());
        assert_eq!(size_from(Some(&json!([10, 8]))), Some([10.0, 8.0]));
        assert_eq!(size_from(Some(&json!([0, 8]))), None);
    }

    #[test]
    fn keys_and_names() {
        assert!(key_ok(&SecretKey::generate().public().to_string()));
        assert!(!key_ok("") && !key_ok("o1a2b3c4") && !key_ok(&"z".repeat(64)));
        assert_eq!(clip_name("Ada\n Lovelace and a very long name indeed"), "Ada Lovelace and a very ");
        assert_eq!(unhex32(&hex(&[7u8; 32])), Some([7u8; 32]));
        assert_eq!(unhex32("abc"), None);
    }
}
