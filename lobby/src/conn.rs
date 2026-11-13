//! One visit-client's connection: its hello (version 4: its key proved first), then every message it sends, to the
//! core, and everything the core says, to it. The lobby pings it every `--ping` seconds and drops it when a ping
//! goes unanswered as long (a connection that died without a word).
use std::{net::IpAddr, str::FromStr, sync::Arc, time::Duration};

use axum::extract::ws::{CloseFrame, Message, WebSocket};
use futures_util::{SinkExt, StreamExt};
use lobby_proto::{self as proto, FromLobby};
use serde_json::Value;
use tokio::{sync::{mpsc, oneshot}, time::Instant};
use tracing::debug;

use crate::{core::{hex, Conn, Ev, Out, Proto}, Server};

/// Seconds to say hello (and, version 4, to prove the key).
const HELLO_TIMEOUT: Duration = Duration::from_secs(10);
/// Hellos a connection may try before it is closed.
const HELLO_TRIES: usize = 5;
/// Messages a connection may send: this many a second, in bursts of up to `BURST` (a visit-client sends a few a
/// minute); more, and it is closed (its session held, as for any drop).
const RATE: f64 = 20.0;
const BURST: f64 = 60.0;
/// How long a message may take to go out before its connection is taken for dead (a client that stopped reading).
const SEND_WAIT: Duration = Duration::from_secs(15);
/// 5.3: bytes of pictures a connection may hand over (`media_put`), apart from its messages: this many a second, in
/// bursts of up to a line's worth (4 screenshots). Past it, the picture is refused, the connection kept.
const UP_RATE: f64 = (32 << 20) as f64 / 60.0;
const UP_BURST: f64 = (32 << 20) as f64;

/// 5.3: a picture being handed over: what its `media_put` said it is, and its bytes so far (how many came, and, unless it
/// was refused on the way, the bytes: a refused one's rest is read and dropped, never taken for messages).
struct Upload {
    id: String,
    mime: String,
    size: usize,
    got: usize,
    refused: bool,
    bytes: Vec<u8>,
}

/// A `media_put`, checked: a picture's id, a kind a line carries, a size within that kind's.
fn upload_of(m: &Value) -> Option<Upload> {
    let id = m.get("id").and_then(Value::as_str).filter(|i| proto::media::id_ok(i))?;
    let mime = m.get("mime").and_then(Value::as_str)?;
    let size = m.get("size").and_then(Value::as_u64).filter(|n| *n > 0 && proto::media::max_of(mime).is_some_and(|max| *n as usize <= max))?;
    Some(Upload { id: id.into(), mime: mime.into(), size: size as usize, got: 0, refused: false, bytes: Vec::with_capacity(size as usize) })
}

/// The names a version-4 hello may have signed: the lobby's own (`--hosts`). Without them, only a name of this very
/// machine (`127.0.0.1:<port>`, `localhost`, `[::1]`), as the connection gave it: a Host anyone can send otherwise,
/// and a lobby that trusted it would let another lobby pass its challenge on (6.1).
fn signable(hosts: &[String], host: &str) -> Vec<String> {
    if !hosts.is_empty() {
        return hosts.to_vec();
    }
    let h = proto::authority(host);
    let name = h.rsplit_once(':').map_or(h.as_str(), |(n, p)| if p.bytes().all(|c| c.is_ascii_digit()) { n } else { h.as_str() });
    if ["127.0.0.1", "localhost", "[::1]"].contains(&name) { vec![h] } else { vec![] }
}

enum Phase {
    Hello { tries: usize },
    /// Version 4: the challenge sent, the proof awaited.
    Proving { hello: Value, key: String, nonce: String, tries: usize },
    In(String),
    /// 3.1: a browser, its key's proof awaited (and the web pass it brought, if any)...
    BrowserProving { key: String, pass: Option<proto::WebPass>, switch: bool, verify: Option<String>, forget: bool, nonce: String },
    /// ...and in: linked (speaking for its office), or waiting with a pairing code.
    Browser,
}

fn parse(raw: &str, conn: &Conn) -> Option<Value> {
    let Ok(m) = serde_json::from_str::<Value>(raw) else {
        conn.send(&FromLobby::error("bad_json", "a message is one JSON object", None));
        return None;
    };
    if !m.is_object() || !m.get("type").is_some_and(Value::is_string) {
        conn.send(&FromLobby::error("bad_message", "a message is a JSON object with a string `type`", None));
        return None;
    }
    Some(m)
}

/// Whether `signature` (128 hex) is `key`'s, over `message`.
fn signed(key: &str, message: &str, signature: &str) -> bool {
    let Ok(pk) = iroh_base::PublicKey::from_str(key) else { return false };
    if signature.len() != 128 {
        return false;
    }
    let Some(bytes) = (0..64).map(|i| u8::from_str_radix(signature.get(2 * i..2 * i + 2)?, 16).ok()).collect::<Option<Vec<u8>>>() else { return false };
    pk.verify(message.as_bytes(), &iroh_base::Signature::from_bytes(&bytes.try_into().expect("64 bytes"))).is_ok()
}

/// Whether `signature` (128 hex) is `key`'s, over what a hello to this lobby signs, for any of `hosts`.
fn proved(key: &str, nonce: &str, signature: &str, hosts: &[String]) -> bool {
    hosts.iter().any(|h| signed(key, &proto::proof_message(h, nonce, key), signature))
}

/// A web pass as a browser brings it: its office's key, its time and nonce well formed, signed by that key for one of
/// this lobby's names.
fn pass_good(p: &proto::WebPass, hosts: &[String]) -> bool {
    proto::key_ok(&p.key) && p.nonce.len() == 32 && p.nonce.bytes().all(|c| c.is_ascii_hexdigit())
        && hosts.iter().any(|h| signed(&p.key, &proto::pass_message(h, &p.key, p.expires, &p.nonce), &p.signature))
}

pub async fn run(server: Arc<Server>, socket: WebSocket, ip: IpAddr, host: String, agent: String) {
    let (mut sink, mut stream) = socket.split();
    let (tx, mut rx) = mpsc::channel::<Out>(crate::core::QUEUE);
    let slow = Arc::new(tokio::sync::Notify::new());
    let conn = Conn { id: server.next_conn(), tx, slow: slow.clone() };
    let core = server.core.clone();
    let hosts = signable(&server.cfg.hosts, &host);
    let (mut tokens, mut filled) = (BURST, Instant::now());
    let mut phase = Phase::Hello { tries: 0 };
    let mut deadline = Some(Instant::now() + HELLO_TIMEOUT);
    let ping_every = server.cfg.ping;
    let mut ping = tokio::time::interval_at(Instant::now() + ping_every, ping_every);
    let mut unanswered: Option<Instant> = None;
    let mut close: Option<(u16, &'static str)> = None;
    let (mut upload, mut v4) = (None::<Upload>, false);
    let (mut up_tokens, mut up_filled) = (UP_BURST, Instant::now());
    loop {
        let wait = deadline.unwrap_or_else(|| Instant::now() + Duration::from_secs(3600));
        tokio::select! {
            out = rx.recv() => match out {
                Some(Out::Text(t)) => if !matches!(tokio::time::timeout(SEND_WAIT, sink.send(Message::Text(t))).await, Ok(Ok(()))) { break },
                Some(Out::Binary(b)) => if !matches!(tokio::time::timeout(SEND_WAIT, sink.send(Message::Binary(b))).await, Ok(Ok(()))) { break },
                Some(Out::Close(code, why)) => { close = Some((code, why)); break }
                None => break,
            },
            _ = slow.notified() => {   // it cannot take what it is sent
                close = Some((1008, "too slow: messages were waiting"));
                break;
            }
            m = stream.next() => {
                let raw = match m {
                    Some(Ok(Message::Text(t))) => t.to_string(),
                    Some(Ok(Message::Binary(b))) if upload.is_some() => {   // 5.3: a piece of the picture being handed over
                        let u = upload.as_mut().expect("some");
                        u.got += b.len();
                        if !u.refused {
                            up_tokens = (up_tokens + up_filled.elapsed().as_secs_f64() * UP_RATE).min(UP_BURST);
                            up_filled = Instant::now();
                            if up_tokens < b.len() as f64 {
                                (u.refused, u.bytes) = (true, Vec::new());
                                conn.send(&FromLobby::error("rate_limited", format!("picture {}: at most 32 MiB of pictures a minute; this one is not held", u.id), Some("media_put")));
                            } else {
                                up_tokens -= b.len() as f64;
                                u.bytes.extend_from_slice(&b);
                            }
                        }
                        if u.got < u.size {
                            continue;
                        }
                        let u = upload.take().expect("some");
                        match &phase {
                            _ if u.refused => {}
                            Phase::In(oid) if u.got == u.size && proto::media::is(&u.id, &u.mime, &u.bytes) => {
                                let _ = core.0.send(Ev::MediaPut { conn: conn.id, office: oid.clone(), id: u.id, mime: u.mime, bytes: u.bytes.into() });
                            }
                            _ => conn.send(&FromLobby::error("bad_media", format!("picture {}: its bytes are not that picture (its kind, its size, its id the blake3 hash of them)", u.id), Some("media_put"))),
                        }
                        continue;
                    }
                    Some(Ok(Message::Binary(b))) => String::from_utf8_lossy(&b).into_owned(),
                    Some(Ok(Message::Pong(_))) => { unanswered = None; continue }
                    Some(Ok(Message::Ping(_))) => continue,   // answered as it was read
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(e)) => { debug!("a connection failed: {e}"); break }
                };
                tokens = (tokens + filled.elapsed().as_secs_f64() * RATE).min(BURST);
                filled = Instant::now();
                if tokens < 1.0 {
                    conn.send(&FromLobby::error("rate_limited", format!("at most {RATE} messages a second"), None));
                    close = Some((1008, "too many messages"));
                    break;
                }
                tokens -= 1.0;
                let Some(m) = parse(&raw, &conn) else {
                    if let Phase::Hello { tries } | Phase::Proving { tries, .. } = &mut phase {
                        *tries += 1;
                        if *tries >= HELLO_TRIES { close = Some((1008, "too many tries")); break }
                    }
                    continue;
                };
                let kind = m["type"].as_str().unwrap_or("").to_string();
                phase = match std::mem::replace(&mut phase, Phase::Hello { tries: 0 }) {
                    Phase::In(oid) if kind == "media_put" && v4 => {   // 5.3: its bytes follow, as binary messages
                        upload = upload_of(&m);
                        if upload.is_none() {
                            conn.send(&FromLobby::error("bad_media", format!("media_put is {{ id (64 hex), mime (one of {:?} or {:?}), size (at most 8 MiB; a voice message 2 MiB) }}",
                                proto::media::PICTURE_KINDS, proto::media::VOICE_KINDS), Some("media_put")));
                        }
                        Phase::In(oid)
                    }
                    Phase::In(oid) => {
                        let _ = core.0.send(Ev::Msg { conn: conn.id, office: oid.clone(), m });
                        Phase::In(oid)
                    }
                    Phase::Browser => {
                        let _ = core.0.send(Ev::BrowserMsg { conn: conn.id, m });
                        Phase::Browser
                    }
                    Phase::BrowserProving { key, pass, switch, verify, forget, nonce } => {
                        let sig = m.get("signature").and_then(Value::as_str).unwrap_or("");
                        if kind != "prove" || !hosts.iter().any(|h| signed(&key, &proto::browser_message(h, &nonce, &key), sig)) {
                            conn.send(&FromLobby::error("bad_proof", "prove signs the browser's challenge with its key", Some("prove")));
                            close = Some((4401, "bad proof"));
                            break;
                        }
                        let (reply, rx) = oneshot::channel();
                        let pass = pass.map(|p| (p.key, p.expires, p.nonce));
                        let _ = core.0.send(Ev::BrowserHello { conn: conn.clone(), key, agent: agent.clone(), ip, pass, switch, verify, forget, reply });
                        if !rx.await.unwrap_or(false) {
                            break;   // refused, and closed by the core
                        }
                        deadline = None;
                        Phase::Browser
                    }
                    Phase::Hello { tries } => {
                        let p = m.get("proto").and_then(Value::as_str);
                        if kind != "hello" {
                            conn.send(&FromLobby::error("hello_first", "say hello first", Some(&kind)));
                            Phase::Hello { tries: tries + 1 }
                        } else if p == Some(proto::PROTO4) && hosts.is_empty() {
                            conn.send(&FromLobby::error("proto_unsupported", "this lobby takes version 4 only by the names it is told (lobby --hosts)", Some("hello")));
                            close = Some((1008, "no --hosts"));
                            break;
                        } else if p == Some(proto::PROTO4) && m.get("browser").is_some() {
                            // 3.1: a browser, with its own key (to prove), and a web pass its office made, if it has one.
                            let key = m.get("browser").and_then(Value::as_str).filter(|k| proto::key_ok(k) && iroh_base::PublicKey::from_str(k).is_ok());
                            let pass = match m.get("pass") {
                                None | Some(Value::Null) => Ok(None),
                                Some(w) => serde_json::from_value::<proto::WebPass>(w.clone()).ok().filter(|p| pass_good(p, &hosts)).map(Some).ok_or(()),
                            };
                            match (key, pass) {
                                (None, _) => {
                                    conn.send(&FromLobby::error("bad_message", "browser is the browser's own key (Ed25519, 64 hex characters)", Some("hello")));
                                    Phase::Hello { tries: tries + 1 }
                                }
                                (Some(_), Err(())) => {
                                    conn.send(&FromLobby::error("bad_proof", "this link is not this lobby's, or not its workroom's: open the lobby from your Visit panel again", Some("hello")));
                                    close = Some((4401, "bad pass"));
                                    break;
                                }
                                (Some(k), Ok(pass)) => {
                                    let nonce = hex(16);
                                    conn.send(&FromLobby::Challenge { nonce: nonce.clone() });
                                    deadline = Some(Instant::now() + HELLO_TIMEOUT);
                                    let switch = m.get("switch") == Some(&Value::Bool(true));
                                    let verify = m.get("verify").and_then(Value::as_str).map(|v| v.chars().take(16).collect());
                                    let forget = m.get("forget") == Some(&Value::Bool(true));
                                    Phase::BrowserProving { key: k.to_string(), pass, switch, verify, forget, nonce }
                                }
                            }
                        } else if p == Some(proto::PROTO4) {
                            match m.get("key").and_then(Value::as_str).filter(|k| proto::key_ok(k) && iroh_base::PublicKey::from_str(k).is_ok()) {
                                Some(k) => {
                                    let nonce = hex(16);
                                    conn.send(&FromLobby::Challenge { nonce: nonce.clone() });
                                    deadline = Some(Instant::now() + HELLO_TIMEOUT);
                                    Phase::Proving { key: k.to_string(), hello: m, nonce, tries }
                                }
                                None => {
                                    conn.send(&FromLobby::error("bad_message", "key is this office's key: its endpoint's, 64 hex characters", Some("hello")));
                                    Phase::Hello { tries: tries + 1 }
                                }
                            }
                        } else if p == Some(proto::PROTO3) && server.cfg.v3 {
                            match ask_hello(&core, &conn, Proto::V3, m, None).await {
                                Some(oid) => { deadline = None; Phase::In(oid) }
                                None => Phase::Hello { tries: tries + 1 },
                            }
                        } else {
                            let speaks = if server.cfg.v3 { format!("{} and {}", proto::PROTO4, proto::PROTO3) } else { proto::PROTO4.to_string() };
                            conn.send(&FromLobby::error("proto_unsupported", format!("this lobby speaks {speaks}"), Some("hello")));
                            Phase::Hello { tries: tries + 1 }
                        }
                    }
                    Phase::Proving { hello, key, nonce, tries } => {
                        let sig = m.get("signature").and_then(Value::as_str).unwrap_or("");
                        if kind != "prove" || !proved(&key, &nonce, sig, &hosts) {
                            conn.send(&FromLobby::error("bad_proof", format!("prove signs {:?} with the key's secret half (the lobby dialled: one of {hosts:?})",
                                proto::proof_message("<lobby>", "<nonce>", "<key>")), Some("prove")));
                            close = Some((4401, "bad proof"));
                            break;
                        }
                        match ask_hello(&core, &conn, Proto::V4, hello, Some(key)).await {
                            Some(oid) => { deadline = None; v4 = true; Phase::In(oid) }
                            None => Phase::Hello { tries: tries + 1 },
                        }
                    }
                };
                if let Phase::Hello { tries } = phase {
                    if tries >= HELLO_TRIES { close = Some((1008, "too many tries")); break }
                    if tries > 0 { deadline = Some(Instant::now() + HELLO_TIMEOUT) }
                }
            }
            _ = tokio::time::sleep_until(wait), if deadline.is_some() => {
                conn.send(&FromLobby::error("hello_timeout", format!("say hello within {} seconds", HELLO_TIMEOUT.as_secs()), None));
                close = Some((1008, "no hello"));
                break;
            }
            _ = ping.tick() => {
                if let Some(since) = unanswered {
                    if since.elapsed() >= ping_every {
                        close = Some((1011, "keepalive ping timeout"));
                        break;
                    }
                } else {
                    unanswered = Some(Instant::now());
                    if !matches!(tokio::time::timeout(SEND_WAIT, sink.send(Message::Ping(Default::default()))).await, Ok(Ok(()))) { break }
                }
            }
        }
    }
    // What the core said before it asked for the close goes out first (an error, a last view).
    let flush = async {
        let mut close = close;
        while let Ok(o) = rx.try_recv() {
            match o {
                Out::Text(t) => if sink.send(Message::Text(t)).await.is_err() { return },
                Out::Binary(b) => if sink.send(Message::Binary(b)).await.is_err() { return },
                Out::Close(code, why) => { close = close.or(Some((code, why))); break }   // the core's own: said too
            }
        }
        if let Some((code, why)) = close {
            let _ = sink.send(Message::Close(Some(CloseFrame { code, reason: why.into() }))).await;
        }
        let _ = sink.close().await;
    };
    let _ = tokio::time::timeout(SEND_WAIT, flush).await;
    match phase {
        Phase::In(oid) => { let _ = core.0.send(Ev::Closed { conn: conn.id, office: oid }); }
        Phase::Browser => { let _ = core.0.send(Ev::BrowserClosed { conn: conn.id }); }
        _ => {}
    }
    server.left(ip);
}

#[cfg(test)]
mod tests {
    use super::signable;

    #[test]
    fn a_hello_signs_the_lobbys_own_names_only() {
        let given = vec!["basedverse.app".to_string()];
        assert_eq!(signable(&given, "evil.example"), given, "--hosts: those, whatever Host came");
        assert_eq!(signable(&[], "127.0.0.1:8765"), vec!["127.0.0.1:8765"]);
        assert_eq!(signable(&[], "localhost:9000"), vec!["localhost:9000"]);
        assert_eq!(signable(&[], "[::1]:9000"), vec!["[::1]:9000"]);
        assert!(signable(&[], "evil.example").is_empty(), "no --hosts: a Host anyone can send is no name of this lobby");
        assert!(signable(&[], "127.0.0.1.evil.example").is_empty());
    }
}

async fn ask_hello(core: &crate::core::Handle, conn: &Conn, proto: Proto, hello: Value, key: Option<String>) -> Option<String> {
    let (reply, rx) = oneshot::channel();
    core.0.send(Ev::Hello { conn: conn.clone(), proto, hello, key, reply }).ok()?;
    rx.await.ok().flatten()
}
