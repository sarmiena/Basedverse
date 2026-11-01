//! The lobby session (../SPEC.md section 6): one WebSocket to the lobby, `basedverse-lobby/4`. Its hello names the
//! office's key and proves it: the lobby sends a challenge, and this signs it with the key's secret half, naming the
//! lobby it dialled (lobby_proto::proof_message), so no other lobby can pass the challenge on. It reconnects by
//! itself (1 s, 2 s, 4 s, ... up to 30 s) and resumes its session with the secret from the last welcome, so a drop
//! shorter than the lobby's resume window keeps the office, its knocks and its visits. Everything the lobby says
//! goes to the core; the core's messages go to the lobby. The lobby pings every 20 s: a connection silent for a
//! minute (a laptop waking, a network gone) is given up and made again.
//!
//! It is in the lobby only while the workroom has someone signed in (../../LOBBY.md 2, rule 3: a live session of its
//! sign-in) and its page has not taken it out (away). Either one gone, it says bye and stays out until both hold.
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use iroh::SecretKey;
use lobby_proto::{self as proto, About, ToLobby};
use serde_json::Value;
use tokio::{sync::mpsc, time::Instant};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{info, warn};

use crate::core::Ev;

pub const PROTO: &str = proto::PROTO4;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const SILENCE: Duration = Duration::from_secs(60);

/// What the core asks of the lobby session.
pub enum LobbyOut {
    /// A message for the lobby.
    Msg(ToLobby),
    /// Leave the lobby and stay out (away), until `Join`.
    Leave,
    /// Come back after `Leave`: a new session.
    Join,
    /// Someone signed in on this workroom, or nobody any more: in the lobby only while someone is.
    SignedIn(bool),
    /// Leave for good (the visit-client is stopping).
    Bye,
}

/// How this office introduces itself; kept current (its status, its visitors, its name as the lobby last
/// showed it, its line in the list of who is here), so a new session starts as the office now is.
pub struct Hello {
    pub name: String,
    pub sim: Value,
    pub status: String,
    pub max_visitors: u8,
    pub about: Option<About>,
}

/// Why it is not in the lobby now, if it is not.
fn out(away: bool, signed_in: bool) -> Option<&'static str> {
    if away {
        Some("away: out of the lobby until the page asks to come back")
    } else if !signed_in {
        Some("nobody signed in on this workroom: out of the lobby until someone is")
    } else {
        None
    }
}

/// What a message changes of how this office introduces itself, kept for the next hello (sent or not: one made while
/// out of the lobby goes with the next hello).
fn remember(hello: &mut Hello, m: &ToLobby) {
    match m {
        ToLobby::Status { status: Some(s) } => hello.status = s.clone(),
        ToLobby::MaxVisitors { n: Some(n) } => hello.max_visitors = (*n).min(8),
        ToLobby::About { about } => hello.about = about.clone(),
        _ => {}
    }
}

fn text(m: &ToLobby) -> Message {
    Message::Text(m.to_json().to_string().into())
}

/// Until `Bye`. `away`: start out of the lobby (as the page last left it), until `Join`. Out of it too until someone
/// is signed in (`SignedIn(true)`).
pub async fn run(url: String, key: SecretKey, mut hello: Hello, mut rx: mpsc::UnboundedReceiver<LobbyOut>, core: mpsc::UnboundedSender<Ev>, mut away: bool) {
    let (mut resume, mut backoff, mut me) = (None::<String>, 1u64, None::<String>);
    let public = key.public().to_string();
    let host = proto::authority(&url);
    let mut signed_in = false;
    loop {
        if let Some(why) = out(away, signed_in) {
            info!("{why}");
            while out(away, signed_in).is_some() {
                match rx.recv().await {
                    Some(LobbyOut::Join) => away = false,
                    Some(LobbyOut::Leave) => away = true,
                    Some(LobbyOut::SignedIn(s)) => signed_in = s,
                    Some(LobbyOut::Bye) | None => return,
                    Some(LobbyOut::Msg(m)) => remember(&mut hello, &m),   // nothing to send while out
                }
            }
            (resume, backoff) = (None, 1);
        }
        // Connecting: asked to leave meanwhile (or signed out), it gives up at once, before any hello.
        let connect = tokio::time::timeout(CONNECT_TIMEOUT, connect_async(url.as_str()));
        tokio::pin!(connect);
        let attempt = loop {
            tokio::select! {
                r = &mut connect => break Some(r),
                o = rx.recv() => match o {
                    Some(LobbyOut::Leave) => { away = true; break None }
                    Some(LobbyOut::SignedIn(s)) => { signed_in = s; if !s { break None } }
                    Some(LobbyOut::Join) => {}
                    Some(LobbyOut::Msg(m)) => remember(&mut hello, &m),
                    Some(LobbyOut::Bye) | None => return,
                },
            }
        };
        let Some(attempt) = attempt else { continue };
        match attempt {
            Ok(Ok((ws, _))) => {
                let (mut tx, mut rx_ws) = ws.split();
                let first = ToLobby::Hello {
                    proto: PROTO.into(), name: hello.name.clone(), key: Some(public.clone()), sim: hello.sim.clone(), status: hello.status.clone(),
                    max_visitors: hello.max_visitors, resume: resume.clone(), world: false, links: false, chat: false, whisper: false,
                    about: hello.about.clone(),
                };
                let mut left = false;
                if tx.send(text(&first)).await.is_ok() {
                    let (mut welcomed, mut heard) = (false, Instant::now());
                    loop {
                        tokio::select! {
                            m = rx_ws.next() => {
                                heard = Instant::now();   // pings count: tungstenite answers them as it reads
                                match m {
                                    Some(Ok(Message::Text(t))) => {
                                        let Ok(v) = serde_json::from_str::<Value>(t.as_str()) else { continue };
                                        let kind = v.get("type").and_then(Value::as_str).unwrap_or("").to_string();
                                        if kind == "challenge" {   // the key proved: the lobby's nonce, signed
                                            let nonce = v.get("nonce").and_then(Value::as_str).unwrap_or("");
                                            let signature = key.sign(proto::proof_message(&host, nonce, &public).as_bytes());
                                            let hex: String = signature.to_bytes().iter().map(|b| format!("{b:02x}")).collect();
                                            if tx.send(text(&ToLobby::Prove { signature: hex })).await.is_err() { break }
                                            continue;
                                        }
                                        if kind == "welcome" {
                                            resume = v.get("resume").and_then(Value::as_str).map(str::to_string);
                                            me = v.get("id").and_then(Value::as_str).map(str::to_string);
                                            welcomed = true;
                                            backoff = 1;
                                        } else if kind == "lobby" {   // its name as the lobby shows it (after a rename)
                                            let mine = v.get("offices").and_then(Value::as_array).into_iter().flatten()
                                                .find(|o| o.get("id").and_then(Value::as_str) == me.as_deref());
                                            if let Some(name) = mine.and_then(|o| o.get("name")).and_then(Value::as_str) {
                                                hello.name = name.to_string();
                                            }
                                        }
                                        let refused = !welcomed && kind == "error";
                                        let code = v.get("code").and_then(Value::as_str).unwrap_or("").to_string();
                                        let _ = core.send(Ev::Lobby(v));   // a refusal too: the page says why
                                        if refused {
                                            warn!(code, "the lobby refused this office");
                                            if code == "name_invalid" || code == "proto_unsupported" {
                                                let _ = core.send(Ev::Fatal(4));
                                                return;
                                            }
                                            if code == "banned" || code == "bad_proof" {
                                                backoff = 30;   // not soon again: a ban lasts, and a proof refused will be again
                                            }
                                            break;   // name_taken: its old session may still be held; try again later
                                        }
                                    }
                                    Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                                    Some(Ok(_)) => {}
                                }
                            }
                            _ = tokio::time::sleep_until(heard + SILENCE) => {
                                warn!("the lobby has been silent for a minute: connecting again");
                                break;
                            }
                            o = rx.recv() => match o {
                                Some(LobbyOut::Msg(m)) => {
                                    remember(&mut hello, &m);
                                    // Before its welcome, nothing but the hello and its proof: what changed goes with it.
                                    if welcomed && tx.send(text(&m)).await.is_err() { break }
                                }
                                Some(o @ (LobbyOut::Leave | LobbyOut::SignedIn(false))) => {   // the session ends at once: nothing to resume
                                    if matches!(o, LobbyOut::Leave) { away = true } else { signed_in = false }
                                    let _ = tx.send(text(&ToLobby::Bye)).await;
                                    let _ = tx.close().await;
                                    left = true;
                                    break;
                                }
                                Some(LobbyOut::Join | LobbyOut::SignedIn(true)) => {}
                                Some(LobbyOut::Bye) | None => {
                                    let _ = tx.send(text(&ToLobby::Bye)).await;
                                    let _ = tx.close().await;
                                    return;
                                }
                            },
                        }
                    }
                }
                let _ = core.send(Ev::LobbyDown);
                if left {
                    continue;
                }
            }
            Ok(Err(e)) => warn!("cannot reach the lobby: {e}"),
            Err(_) => warn!("the lobby did not answer in {} s", CONNECT_TIMEOUT.as_secs()),
        }
        info!("connecting to the lobby again in {backoff} s");
        // Waiting to try again; asked to leave meanwhile (or signed out), it stays out instead.
        let wait = tokio::time::sleep(Duration::from_secs(backoff));
        tokio::pin!(wait);
        loop {
            tokio::select! {
                _ = &mut wait => break,
                o = rx.recv() => match o {
                    Some(LobbyOut::Leave) => { away = true; break }
                    Some(LobbyOut::SignedIn(s)) => { signed_in = s; if !s { break } }
                    Some(LobbyOut::Bye) | None => return,
                    Some(LobbyOut::Msg(m)) => remember(&mut hello, &m),
                    Some(LobbyOut::Join) => {}
                },
            }
        }
        backoff = (backoff * 2).clamp(1, 30);
    }
}
