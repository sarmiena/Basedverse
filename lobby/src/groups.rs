//! What the lobby keeps (../../LOBBY.md 2, rule 5): its world groups, by key, and its bans. Each a JSON file in the
//! lobby's data directory (`--data-dir`), written whole on every change (to a new file, then moved over the old,
//! so a crash leaves one or the other); without a data directory, in memory only, gone at a restart.
//!
//! A world group's members may each put the others' plots in their worlds (SPEC 8.4): the lobby says who is in it,
//! and each member's visit-client takes its word (LOBBY.md 2, rule 7). Whoever makes a group owns it; its owner
//! names ops; ops (and the lobby's operators) answer requests to join, take members out, and change it. An open
//! group takes anyone who asks. The owner leaving hands it to its first op, else its oldest member; the last
//! member leaving ends it.
use std::{collections::BTreeMap, path::PathBuf};

use anyhow::Context;
use lobby_proto::{self as proto, Ban, FromLobby, Limits, Listed, Member, Request};
use serde::{Deserialize, Serialize};
use tracing::warn;

use crate::core::now_unix;

/// The most groups a lobby keeps.
const MOST_GROUPS: usize = 10_000;

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Group {
    name: String,
    about: String,
    open: bool,
    owner: String,
    ops: Vec<String>,
    /// In the order they joined.
    members: Vec<Member>,
    pending: Vec<Request>,
    created: u64,
}

/// A refusal: its code and what it says.
pub type No = (&'static str, String);

/// What a change means: whom to tell what (by key), and which group everyone in it hears whole.
#[derive(Default)]
pub struct Done {
    pub tell: Vec<(String, FromLobby)>,
    pub changed: Option<String>,
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

pub struct Groups {
    path: Option<PathBuf>,
    /// By name in lower case.
    groups: BTreeMap<String, Group>,
}

impl Groups {
    pub fn open(dir: Option<&std::path::Path>) -> anyhow::Result<Groups> {
        let path = dir.map(|d| d.join("groups.json"));
        let list: Vec<Group> = read_json(&path)?;
        Ok(Groups { path, groups: list.into_iter().map(|g| (g.name.to_ascii_lowercase(), g)).collect() })
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

    /// A group's name as it was made, from any case of it.
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

    /// The groups `key` is in.
    pub fn of(&self, key: &str) -> Vec<String> {
        self.groups.values().filter(|g| g.members.iter().any(|m| m.key == key)).map(|g| g.name.clone()).collect()
    }

    fn count_of(&self, key: &str) -> usize {
        self.groups.values().filter(|g| g.members.iter().any(|m| m.key == key)).count()
    }

    /// A key's name, as it now goes by, in every group it is in or asks to be.
    pub fn seen(&mut self, key: &str, name: &str) {
        let mut changed = false;
        for g in self.groups.values_mut() {
            for m in g.members.iter_mut().filter(|m| m.key == key && m.name != name) {
                m.name = name.into();
                changed = true;
            }
            for r in g.pending.iter_mut().filter(|r| r.key == key && r.name != name) {
                r.name = name.into();
                changed = true;
            }
        }
        if changed {
            self.save();
        }
    }

    fn is_op(g: &Group, key: &str, admin: bool) -> bool {
        admin || g.owner == key || g.ops.iter().any(|o| o == key)
    }

    /// A group as `key` hears it: its requests only for its ops.
    pub fn view(&self, group: &str, key: &str) -> FromLobby {
        let g = self.get(group).expect("a group");
        FromLobby::Group {
            name: g.name.clone(), about: g.about.clone(), open: g.open, owner: g.owner.clone(), ops: g.ops.clone(), members: g.members.clone(),
            pending: Self::is_op(g, key, false).then(|| g.pending.clone()),
        }
    }

    /// The directory: every group, its size and how many of it are online.
    pub fn listed(&self, online: impl Fn(&str) -> bool) -> Vec<Listed> {
        self.groups.values().map(|g| Listed {
            name: g.name.clone(), about: g.about.clone(), open: g.open, members: g.members.len(),
            online: g.members.iter().filter(|m| online(&m.key)).count(),
        }).collect()
    }

    fn named(&self, group: Option<String>) -> Result<String, No> {
        group.and_then(|g| self.find(&g)).ok_or(("not_found", "no group of that name".into()))
    }

    fn about(about: Option<String>, lim: &Limits) -> String {
        proto::plain(about.as_deref().unwrap_or(""), lim.about.unwrap_or(200))
    }

    pub fn create(&mut self, me: &str, name: &str, group: Option<String>, about: Option<String>, open: bool, lim: &Limits) -> Result<Done, No> {
        let Some(group) = group.filter(|g| proto::name_ok(g)) else {
            return Err(("name_invalid", "a group's name is 1-24 letters, digits, spaces, _ . -, starting and ending with a letter or digit".into()));
        };
        if self.find(&group).is_some() {
            return Err(("name_taken", format!("there is a group called {group} already")));
        }
        if self.count_of(me) >= lim.groups.unwrap_or(20) {
            return Err(("too_many", format!("you are in {} groups already", lim.groups.unwrap_or(20))));
        }
        if self.groups.len() >= MOST_GROUPS {
            return Err(("full", "this lobby has all the groups it keeps".into()));
        }
        let now = now_unix();
        let g = Group { name: group.clone(), about: Self::about(about, lim), open, owner: me.into(), ops: vec![],
                        members: vec![Member { key: me.into(), name: name.into(), since: now }], pending: vec![], created: now };
        self.groups.insert(group.to_ascii_lowercase(), g);
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    pub fn join(&mut self, me: &str, name: &str, group: Option<String>, lim: &Limits) -> Result<Done, No> {
        let group = self.named(group)?;
        let (most, waiting) = (lim.group_members.unwrap_or(64), lim.group_pending.unwrap_or(50));
        let mine = self.count_of(me);
        let g = self.get_mut(&group).expect("found");
        if g.members.iter().any(|m| m.key == me) {
            return Err(("already_member", format!("you are in {group} already")));
        }
        if g.pending.iter().any(|r| r.key == me) {
            return Err(("pending", format!("your request to join {group} is waiting")));
        }
        let asking = self.groups.values().filter(|g| g.pending.iter().any(|r| r.key == me)).count();
        let g = self.get_mut(&group).expect("found");
        if mine >= lim.groups.unwrap_or(20) {
            return Err(("too_many", format!("you are in {} groups already", lim.groups.unwrap_or(20))));
        }
        if g.members.len() >= most {
            return Err(("full", format!("{group} has all the members it takes")));
        }
        let mut done = Done::default();
        if g.open {
            g.members.push(Member { key: me.into(), name: name.into(), since: now_unix() });
        } else {
            if g.pending.len() >= waiting {
                return Err(("full", format!("{group} has all the requests it takes waiting")));
            }
            if asking >= lim.groups.unwrap_or(20) {
                return Err(("too_many", "you have all the requests you may have waiting: wait for their answers, or take some back".into()));
            }
            let r = Request { key: me.into(), name: name.into() };
            g.pending.push(r.clone());
            for op in std::iter::once(&g.owner).chain(&g.ops) {
                done.tell.push((op.clone(), FromLobby::GroupRequest { group: group.clone(), from: r.clone() }));
            }
            done.tell.push((me.into(), FromLobby::GroupAnswered { group: group.clone(), accept: None }));
        }
        done.changed = Some(group);
        self.save();
        Ok(done)
    }

    /// Leaving a group (or taking back a request to join it).
    pub fn leave(&mut self, me: &str, group: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        let mut done = Done::default();
        if let Some(i) = g.pending.iter().position(|r| r.key == me) {
            g.pending.remove(i);
            done.tell.push((me.into(), FromLobby::GroupAnswered { group: group.clone(), accept: Some(false) }));
            done.changed = Some(group);
            self.save();
            return Ok(done);
        }
        let Some(i) = g.members.iter().position(|m| m.key == me) else { return Err(("not_found", format!("you are not in {group}"))) };
        g.members.remove(i);
        g.ops.retain(|o| o != me);
        done.tell.push((me.into(), FromLobby::GroupLeft { group: group.clone(), reason: "left".into() }));
        if g.owner == me {
            match g.ops.first().cloned().or_else(|| g.members.first().map(|m| m.key.clone())) {
                Some(next) => {
                    g.ops.retain(|o| *o != next);
                    g.owner = next;
                }
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

    pub fn answer(&mut self, me: &str, admin: bool, group: Option<String>, key: Option<String>, accept: Option<bool>, lim: &Limits) -> Result<Done, No> {
        let group = self.named(group)?;
        let Some(accept) = accept else { return Err(("bad_message", "accept must be true or false".into())) };
        let most = lim.group_members.unwrap_or(64);
        let theirs = key.as_deref().map_or(0, |k| self.count_of(k));
        let g = self.get_mut(&group).expect("found");
        if !Self::is_op(g, me, admin) {
            return Err(("not_op", format!("only {group}'s ops answer its requests")));
        }
        let Some(i) = key.as_ref().and_then(|k| g.pending.iter().position(|r| r.key == *k)) else {
            return Err(("not_found", "no request from that key".into()));
        };
        if accept && g.members.len() >= most {
            return Err(("full", format!("{group} has all the members it takes")));
        }
        if accept && theirs >= lim.groups.unwrap_or(20) {
            return Err(("too_many", "they are in all the groups they may be".into()));
        }
        let r = g.pending.remove(i);
        if accept {
            g.members.push(Member { key: r.key.clone(), name: r.name.clone(), since: now_unix() });
        }
        self.save();
        Ok(Done { tell: vec![(r.key, FromLobby::GroupAnswered { group: group.clone(), accept: Some(accept) })], changed: Some(group) })
    }

    pub fn remove(&mut self, me: &str, admin: bool, group: Option<String>, key: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        if !Self::is_op(g, me, admin) {
            return Err(("not_op", format!("only {group}'s ops take members out")));
        }
        let Some(key) = key.filter(|k| g.members.iter().any(|m| m.key == *k)) else { return Err(("not_found", format!("no member of {group} with that key"))) };
        if key == g.owner {
            return Err(("not_op", "its owner cannot be taken out".into()));
        }
        if g.ops.contains(&key) && g.owner != me && !admin {
            return Err(("not_op", "only its owner takes an op out".into()));
        }
        g.members.retain(|m| m.key != key);
        g.ops.retain(|o| *o != key);
        self.save();
        Ok(Done { tell: vec![(key, FromLobby::GroupLeft { group: group.clone(), reason: "removed".into() })], changed: Some(group) })
    }

    pub fn set(&mut self, me: &str, admin: bool, group: Option<String>, about: Option<String>, open: Option<bool>, lim: &Limits) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get_mut(&group).expect("found");
        if !Self::is_op(g, me, admin) {
            return Err(("not_op", format!("only {group}'s ops change it")));
        }
        if about.is_some() {
            g.about = Self::about(about, lim);
        }
        if let Some(o) = open {
            g.open = o;
        }
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    pub fn op(&mut self, me: &str, admin: bool, group: Option<String>, key: Option<String>, op: Option<bool>) -> Result<Done, No> {
        let group = self.named(group)?;
        let Some(op) = op else { return Err(("bad_message", "op must be true or false".into())) };
        let g = self.get_mut(&group).expect("found");
        if g.owner != me && !admin {
            return Err(("not_op", format!("only {group}'s owner names its ops")));
        }
        let Some(key) = key.filter(|k| g.members.iter().any(|m| m.key == *k) && *k != g.owner) else {
            return Err(("not_found", format!("no member of {group} with that key (its owner aside)")));
        };
        g.ops.retain(|o| *o != key);
        if op {
            g.ops.push(key);
        }
        self.save();
        Ok(Done { tell: vec![], changed: Some(group) })
    }

    pub fn delete(&mut self, me: &str, admin: bool, group: Option<String>) -> Result<Done, No> {
        let group = self.named(group)?;
        let g = self.get(&group).expect("found");
        if g.owner != me && !admin {
            return Err(("not_op", format!("only {group}'s owner ends it")));
        }
        let tell = g.members.iter().map(|m| (m.key.clone(), FromLobby::GroupLeft { group: group.clone(), reason: "deleted".into() })).collect();
        self.groups.remove(&group.to_ascii_lowercase());
        self.save();
        Ok(Done { tell, changed: None })
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
