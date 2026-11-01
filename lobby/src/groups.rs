//! What the lobby keeps (../../LOBBY.md 2, rule 5): its worlds (the protocol's groups), by key, its bans, and its
//! linked browsers. Each a JSON file in the lobby's data directory (`--data-dir`), written whole on every change (to
//! a new file, then moved over the old, so a crash leaves one or the other); without a data directory, in memory
//! only, gone at a restart.
//!
//! A world's members each put the others' plots in their worlds (SPEC 8.4): the lobby says who is in it, and each
//! member's visit-client takes its word (LOBBY.md 2, rule 7). A world is public (anyone joins) or takes a password;
//! whoever makes one owns it, and its owner (or the lobby's operators) changes it, takes members out, keeps keys out,
//! and hands it on. The owner leaving hands it to its oldest member; the last member leaving ends it. A world made
//! before 3.1 that took requests is closed until its owner makes it public or gives it a password.
use std::{collections::BTreeMap, path::PathBuf};

use anyhow::Context;
use lobby_proto::{self as proto, Ban, FromLobby, Limits, Linked, Listed, Member};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tracing::warn;

use crate::core::now_unix;

/// The most worlds a lobby keeps.
const MOST_GROUPS: usize = 10_000;
/// Keys one world keeps out, at most.
const MOST_BANS: usize = 500;
/// Browsers linked, at most, lobby-wide (past it, the one unused longest goes).
const MOST_LINKS: usize = 50_000;

fn yes() -> bool {
    true
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Group {
    name: String,
    about: String,
    /// Public: anyone joins (none with a password).
    #[serde(default = "yes")]
    open: bool,
    /// Its password's Argon2id hash (PHC), when it takes one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    password: Option<String>,
    owner: String,
    /// In the order they joined.
    members: Vec<Member>,
    /// Keys kept out.
    #[serde(default)]
    bans: Vec<String>,
    created: u64,
    /// 3.0's ops and requests: none since 3.1, written empty so a 3.0 lobby can still read the file (a rollback).
    #[serde(default)]
    ops: Vec<String>,
    #[serde(default)]
    pending: Vec<Value>,
}

/// A refusal: its code and what it says.
pub type No = (&'static str, String);

/// What a change means: whom to tell what (by key), and which world everyone in it hears whole.
#[derive(Default)]
pub struct Done {
    pub tell: Vec<(String, FromLobby)>,
    pub changed: Option<String>,
}

/// What joining a world takes, before it is tried.
pub enum Joining {
    /// Nothing more: public.
    Open,
    /// Its password, checked against this hash.
    Password(String),
}

fn write_json<T: Serialize>(path: &Option<PathBuf>, what: &T) {
    let Some(p) = path else { return };
    let tmp = p.with_extension("json.new");
    let done = serde_json::to_vec(what).map_err(anyhow::Error::from)
        .and_then(|b| std::fs::write(&tmp, b).context("write"))
        .and_then(|_| std::fs::rename(&tmp, p).context("rename"));
    if let Err(e) = done {
        warn!("{}: {e:#}", p.display());
    }
}

fn read_json<T: for<'de> Deserialize<'de> + Default>(path: &Option<PathBuf>) -> anyhow::Result<T> {
    let Some(p) = path else { return Ok(T::default()) };
    match std::fs::read(p) {
        Ok(b) => serde_json::from_slice(&b).with_context(|| format!("{}: not as this lobby writes it", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(T::default()),
        Err(e) => Err(e).with_context(|| p.display().to_string()),
    }
}

/// A world's password, hashed (Argon2id, a salt of its own), as the lobby keeps it: slow on purpose, so the caller
/// runs it off the lobby's own task.
pub fn hash_password(password: &str) -> Option<String> {
    use argon2::password_hash::{PasswordHasher, SaltString};
    let mut salt = [0u8; 16];
    getrandom::fill(&mut salt).ok()?;
    let salt = SaltString::encode_b64(&salt).ok()?;
    argon2::Argon2::default().hash_password(password.as_bytes(), &salt).ok().map(|h| h.to_string())
}

/// Whether `password` is the one `hash` was made from (as slow as hashing).
pub fn password_is(password: &str, hash: &str) -> bool {
    use argon2::password_hash::{PasswordHash, PasswordVerifier};
    PasswordHash::new(hash).is_ok_and(|h| argon2::Argon2::default().verify_password(password.as_bytes(), &h).is_ok())
}

/// A password as a world takes it: `lim.password` characters, no control characters. None (said why) if not.
pub fn password_ok(p: &str, lim: &Limits) -> Result<(), No> {
    let (least, most) = lim.password.unwrap_or((4, 64));
    let n = p.chars().count();
    if n < least || n > most || p.chars().any(char::is_control) {
        return Err(("bad_password", format!("a world's password is {least} to {most} characters")));
    }
    Ok(())
}

pub struct Groups {
    path: Option<PathBuf>,
    /// By name in lower case.
    groups: BTreeMap<String, Group>,
}

impl Groups {
    pub fn open(dir: Option<&std::path::Path>) -> anyhow::Result<Groups> {
        let path = dir.map(|d| d.join("groups.json"));
        let list: Vec<Group> = read_json(&path)?;
        let groups = list.into_iter().map(|mut g| {
            (g.ops, g.pending) = (vec![], vec![]);   // a 3.0 world's ops and requests: gone (its owner runs it)
            (g.name.to_ascii_lowercase(), g)
        }).collect();
        Ok(Groups { path, groups })
    }

    fn save(&self) {
        write_json(&self.path, &self.groups.values().collect::<Vec<_>>());
    }

    pub fn reset(&mut self) {
        self.groups.clear();
        self.save();
    }

    pub fn names(&self) -> Vec<String> {
        self.groups.values().map(|g| g.name.clone()).collect()
    }

    /// A world's name as it was made, from any case of it.
    pub fn find(&self, name: &str) -> Option<String> {
        self.groups.get(&name.to_ascii_lowercase()).map(|g| g.name.clone())
    }

    fn get(&self, name: &str) -> Option<&Group> {
        self.groups.get(&name.to_ascii_lowercase())
    }

    fn get_mut(&mut self, name: &str) -> Option<&mut Group> {
        self.groups.get_mut(&name.to_ascii_lowercase())
    }

    pub fn is_member(&self, group: &str, key: &str) -> bool {
        self.get(group).is_some_and(|g| g.members.iter().any(|m| m.key == key))
    }

    pub fn member_keys(&self, group: &str) -> Vec<String> {
        self.get(group).map(|g| g.members.iter().map(|m| m.key.clone()).collect()).unwrap_or_default()
    }

    /// The worlds `key` is in.
    pub fn of(&self, key: &str) -> Vec<String> {
        self.groups.values().filter(|g| g.members.iter().any(|m| m.key == key)).map(|g| g.name.clone()).collect()
    }

    fn count_of(&self, key: &str) -> usize {
        self.groups.values().filter(|g| g.members.iter().any(|m| m.key == key)).count()
    }

    /// A key's name, as it now goes by, in every world it is in.
    pub fn seen(&mut self, key: &str, name: &str) {
        let mut changed = false;
        for g in self.groups.values_mut() {
            for m in g.members.iter_mut().filter(|m| m.key == key && m.name != name) {
                m.name = name.into();
                changed = true;
            }
        }
        if changed {
            self.save();
        }
    }

    fn runs(g: &Group, key: &str, admin: bool) -> bool {
        admin || g.owner == key
    }

    /// A world as `key` hears it: its bans only for its owner.
    pub fn view(&self, group: &str, key: &str) -> FromLobby {
        let g = self.get(group).expect("a world");
        FromLobby::Group {
            name: g.name.clone(), about: g.about.clone(), open: g.open && g.password.is_none(), locked: g.password.is_some(), owner: g.owner.clone(),
            ops: vec![], members: g.members.clone(), bans: (g.owner == key).then(|| g.bans.clone()),
        }
    }

    /// The directory: every world, its size and how many of it are online.
    pub fn listed(&self, online: impl Fn(&str) -> bool) -> Vec<Listed> {
        self.groups.values().map(|g| Listed {
            name: g.name.clone(), about: g.about.clone(), open: g.open && g.password.is_none(), locked: g.password.is_some(), members: g.members.len(),
            online: g.members.iter().filter(|m| online(&m.key)).count(),
            owner: g.members.iter().find(|m| m.key == g.owner).map(|m| m.name.clone()).unwrap_or_default(),
        }).collect()
    }

    fn named(&self, group: Option<String>) -> Result<String, No> {
        group.and_then(|g| self.find(&g)).ok_or(("not_found", "no world of that name".into()))
    }

    fn about(about: Option<String>, lim: &Limits) -> String {
        proto::plain(about.as_deref().unwrap_or(""), lim.about.unwrap_or(200))
    }

    /// Whether a new world of this name may be made by `me` (before its password is hashed).
    pub fn may_create(&self, me: &str, group: &Option<String>, lim: &Limits) -> Result<String, No> {
        let Some(group) = group.clone().filter(|g| proto::name_ok(g)) else {
            return Err(("name_invalid", "a world's name is 1-24 letters, digits, spaces, _ . -, starting and ending with a letter or digit".into()));
        };
        if self.find(&group).is_some() {
            return Err(("name_taken", format!("there is a world called {group} already")));
        }
        if self.count_of(me) >= lim.groups.unwrap_or(20) {
            return Err(("too_many", format!("you are in {} worlds already", lim.groups.unwrap_or(20))));
        }
        if self.groups.len() >= MOST_GROUPS {
            return Err(("full", "this lobby has all the worlds it keeps".into()));
        }
        Ok(group)
    }

    /// A new world, `me` its owner and only member: public, or with this password's hash.
    pub fn create(&mut self, me: &str, name: &str, group: Option<String>, about: Option<String>, password: Option<String>, lim: &Limits) -> Result<Done, No> {
        let group = self.may_create(me, &group, lim)?;
        let now = now_unix();
        let g = Group { name: group.clone(), about: Self::about(about, lim), open: password.is_none(), password, owner: me.into(),
                        members: vec![Member { key: me.into(), name: name.into(), since: now }], bans: vec![], created: now, ops: vec![], pending: vec![] };
        self.groups.insert(group.to_ascii_lowercase(), g);
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    /// Whether `me` may join this world, and what it takes: its name as made, and nothing more or its password.
    pub fn may_join(&self, me: &str, group: Option<String>, lim: &Limits) -> Result<(String, Joining), No> {
        let group = self.named(group)?;
        let g = self.get(&group).expect("found");
        if g.members.iter().any(|m| m.key == me) {
            return Err(("already_member", format!("you are in {group} already")));
        }
        if g.bans.iter().any(|k| k == me) {
            return Err(("banned", format!("{group}'s owner keeps you out")));
        }
        if self.count_of(me) >= lim.groups.unwrap_or(20) {
            return Err(("too_many", format!("you are in {} worlds already", lim.groups.unwrap_or(20))));
        }
        if g.members.len() >= lim.group_members.unwrap_or(proto::WORLD_SIZE) {
            return Err(("full", format!("{group} has all the members it takes")));
        }
        match (&g.password, g.open) {
            (Some(h), _) => Ok((group, Joining::Password(h.clone()))),
            (None, true) => Ok((group, Joining::Open)),
            (None, false) => Err(("closed", format!("{group} is closed: its owner makes it public, or gives it a password"))),
        }
    }

    /// `me` in: public, or its password found right (`password_right`, checked against the hash `may_join` gave).
    pub fn join(&mut self, me: &str, name: &str, group: Option<String>, password_right: bool, lim: &Limits) -> Result<Done, No> {
        let (group, takes) = self.may_join(me, group, lim)?;
        if matches!(takes, Joining::Password(_)) && !password_right {
            return Err(("wrong_password", format!("that is not {group}'s password")));
        }
        let g = self.get_mut(&group).expect("found");
        g.members.push(Member { key: me.into(), name: name.into(), since: now_unix() });
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    pub fn leave(&mut self, me: &str, group: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        let Some(i) = g.members.iter().position(|m| m.key == me) else { return Err(("not_found", format!("you are not in {group}"))) };
        g.members.remove(i);
        let mut done = Done { tell: vec![(me.into(), FromLobby::GroupLeft { group: group.clone(), reason: "left".into() })], changed: None };
        if g.owner == me {
            match g.members.first() {
                Some(next) => g.owner = next.key.clone(),
                None => {
                    self.groups.remove(&group.to_ascii_lowercase());
                    self.save();
                    return Ok(done);
                }
            }
        }
        done.changed = Some(group);
        self.save();
        Ok(done)
    }

    /// A member out (its owner's, or the lobby's operators'); `ban`: kept out too (the key need not be in it).
    pub fn remove(&mut self, me: &str, admin: bool, group: Option<String>, key: Option<String>, ban: bool) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        if !Self::runs(g, me, admin) {
            return Err(("not_owner", format!("only {group}'s owner takes members out")));
        }
        let Some(key) = key.filter(|k| proto::key_ok(k) && (ban || g.members.iter().any(|m| m.key == *k))) else {
            return Err(("not_found", format!("no member of {group} with that key")));
        };
        if key == g.owner {
            return Err(("not_owner", "its owner cannot be taken out (hand it on first)".into()));
        }
        if ban && g.bans.len() >= MOST_BANS && !g.bans.contains(&key) {
            return Err(("full", format!("{group} keeps {MOST_BANS} keys out already")));
        }
        let was = g.members.len();
        g.members.retain(|m| m.key != key);
        let out = g.members.len() != was;
        if ban {
            if !g.bans.contains(&key) {
                g.bans.push(key.clone());
            }
        }
        self.save();
        let tell = if out { vec![(key, FromLobby::GroupLeft { group: group.clone(), reason: if ban { "banned" } else { "removed" }.into() })] } else { vec![] };
        Ok(Done { tell, changed: Some(group) })
    }

    pub fn unban(&mut self, me: &str, admin: bool, group: Option<String>, key: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        if !Self::runs(g, me, admin) {
            return Err(("not_owner", format!("only {group}'s owner lets keys back in")));
        }
        let Some(key) = key.filter(|k| g.bans.contains(k)) else { return Err(("not_found", format!("{group} does not keep that key out"))) };
        g.bans.retain(|k| *k != key);
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    /// Whether `me` may change this world (before a new password is hashed).
    pub fn may_set(&self, me: &str, admin: bool, group: Option<String>) -> Result<String, No> {
        let group = self.named(group)?;
        if !Self::runs(self.get(&group).expect("found"), me, admin) {
            return Err(("not_owner", format!("only {group}'s owner changes it")));
        }
        Ok(group)
    }

    /// Its line; public (`open: true`: its password gone); or a new password (its hash: members stay).
    pub fn set(&mut self, me: &str, admin: bool, group: Option<String>, about: Option<String>, open: Option<bool>, password: Option<String>, lim: &Limits) -> Result<Done, No> {
        let group = self.may_set(me, admin, group)?;
        let g = self.get_mut(&group).expect("found");
        if open == Some(false) && password.is_none() && g.password.is_none() {
            return Err(("bad_message", "a world is public, or has a password: give it one".into()));
        }
        if about.is_some() {
            g.about = Self::about(about, lim);
        }
        if let Some(h) = password {
            g.password = Some(h);
            g.open = false;
        } else if open == Some(true) {
            g.password = None;
            g.open = true;
        }
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    /// Handed on to another member.
    pub fn owner(&mut self, me: &str, admin: bool, group: Option<String>, key: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        if !Self::runs(g, me, admin) {
            return Err(("not_owner", format!("only {group}'s owner hands it on")));
        }
        let Some(key) = key.filter(|k| g.members.iter().any(|m| m.key == *k)) else { return Err(("not_found", format!("no member of {group} with that key"))) };
        g.owner = key;
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    pub fn delete(&mut self, me: &str, admin: bool, group: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get(&group).expect("found");
        if !Self::runs(g, me, admin) {
            return Err(("not_owner", format!("only {group}'s owner ends it")));
        }
        let tell = g.members.iter().map(|m| (m.key.clone(), FromLobby::GroupLeft { group: group.clone(), reason: "deleted".into() })).collect();
        self.groups.remove(&group.to_ascii_lowercase());
        self.save();
        Ok(Done { tell, changed: None })
    }
}

/// A browser linked to an office (3.1): its own key, the office's, what it said it was, when.
#[derive(Serialize, Deserialize, Clone, Debug)]
struct Link {
    key: String,
    office: String,
    agent: String,
    created: u64,
    used: u64,
}

/// Days a linked browser that never comes back stays linked.
const LINK_DAYS: u64 = 90;

/// The browsers linked to offices (3.1, SPEC 6.11): each browser's key, and the office it speaks for, while that
/// office is in the lobby. Kept until its office unlinks it, it unlinks itself, or it has not come for 90 days.
pub struct Links {
    path: Option<PathBuf>,
    list: Vec<Link>,
}

impl Links {
    pub fn open(dir: Option<&std::path::Path>) -> anyhow::Result<Links> {
        let path = dir.map(|d| d.join("browsers.json"));
        let mut l = Links { list: read_json(&path)?, path };
        l.prune();
        Ok(l)
    }

    fn save(&self) {
        write_json(&self.path, &self.list);
    }

    fn prune(&mut self) {
        let old = now_unix().saturating_sub(LINK_DAYS * 86_400);
        let before = self.list.len();
        self.list.retain(|l| l.used > old);
        if self.list.len() != before {
            self.save();
        }
    }

    /// The office a browser speaks for.
    pub fn office(&self, key: &str) -> Option<String> {
        self.list.iter().find(|l| l.key == key).map(|l| l.office.clone())
    }

    /// A browser linked to `office` (another office's before: now this one's). Past `most` for the office (or
    /// MOST_LINKS in all), the ones unused longest go: their keys, to put out of the lobby.
    pub fn add(&mut self, key: &str, office: &str, agent: &str, most: usize) -> Vec<String> {
        let now = now_unix();
        self.list.retain(|l| l.key != key);
        let mut gone = Vec::new();
        let mut theirs: Vec<(u64, String)> = self.list.iter().filter(|l| l.office == office).map(|l| (l.used, l.key.clone())).collect();
        theirs.sort();
        gone.extend(theirs.into_iter().map(|(_, k)| k).take((self.list.iter().filter(|l| l.office == office).count() + 1).saturating_sub(most)));
        if self.list.len() + 1 > MOST_LINKS {
            let mut all: Vec<(u64, String)> = self.list.iter().filter(|l| !gone.contains(&l.key)).map(|l| (l.used, l.key.clone())).collect();
            all.sort();
            let over = (self.list.len() - gone.len() + 1).saturating_sub(MOST_LINKS);
            gone.extend(all.into_iter().map(|(_, k)| k).take(over));
        }
        self.list.retain(|l| !gone.contains(&l.key));
        self.list.push(Link { key: key.into(), office: office.into(), agent: agent.into(), created: now, used: now });
        self.save();
        gone
    }

    /// It came in (as `agent`, now): written down when it says something new (another browser, or a new day's hour).
    pub fn used(&mut self, key: &str, agent: &str) {
        let now = now_unix();
        if let Some(l) = self.list.iter_mut().find(|l| l.key == key) {
            if l.agent != agent || now.saturating_sub(l.used) >= 3600 {
                l.used = now;
                l.agent = agent.into();
                self.save();
            }
        }
    }

    /// Unlinked; whether it was linked to `office` (any office, None).
    pub fn remove(&mut self, key: &str, office: Option<&str>) -> bool {
        let before = self.list.len();
        self.list.retain(|l| !(l.key == key && office.is_none_or(|o| o == l.office)));
        let gone = self.list.len() != before;
        if gone {
            self.save();
        }
        gone
    }

    /// An office's linked browsers, the latest used first; `online`: which are in the lobby now.
    pub fn of(&self, office: &str, online: impl Fn(&str) -> bool) -> Vec<Linked> {
        let mut v: Vec<Linked> = self.list.iter().filter(|l| l.office == office).map(|l| Linked {
            key: l.key.clone(), agent: l.agent.clone(), pictures: proto::pictures(&l.key), created: l.created, used: l.used, online: online(&l.key),
        }).collect();
        v.sort_by(|a, b| b.used.cmp(&a.used));
        v
    }

    pub fn reset(&mut self) {
        self.list.clear();
        self.save();
    }
}

/// The lobby's bans, by key, lobby-wide: the operators'.
pub struct Bans {
    path: Option<PathBuf>,
    list: Vec<Ban>,
}

impl Bans {
    pub fn open(dir: Option<&std::path::Path>) -> anyhow::Result<Bans> {
        let path = dir.map(|d| d.join("bans.json"));
        Ok(Bans { list: read_json(&path)?, path })
    }

    fn save(&self) {
        write_json(&self.path, &self.list);
    }

    fn prune(&mut self) {
        let now = now_unix();
        let before = self.list.len();
        self.list.retain(|b| b.until.is_none_or(|u| u > now));
        if self.list.len() != before {
            self.save();
        }
    }

    /// The ban on `key`, while it lasts.
    pub fn get(&mut self, key: &str) -> Option<Ban> {
        self.prune();
        self.list.iter().find(|b| b.key == key).cloned()
    }

    pub fn add(&mut self, b: Ban) {
        self.list.retain(|x| x.key != b.key);
        self.list.push(b);
        self.save();
    }

    pub fn remove(&mut self, key: &str) {
        self.list.retain(|x| x.key != key);
        self.save();
    }

    pub fn list(&mut self) -> Vec<Ban> {
        self.prune();
        self.list.clone()
    }

    pub fn reset(&mut self) {
        self.list.clear();
        self.save();
    }
}
