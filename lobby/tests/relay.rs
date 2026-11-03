//! The lobby's relay (src/relay.rs), from outside: the `lobby` binary with --relay-port, two endpoints whose keys are
//! proved in the lobby linking through it with no direct path at all, and one whose key is not turned away.
use std::{io::BufRead, process::Stdio, time::Duration};

use futures_util::{SinkExt, StreamExt};
use iroh::{endpoint::presets, Endpoint, EndpointAddr, RelayMode, RelayUrl, SecretKey};
use lobby_proto::{proof_message, ToLobby, PROTO4};
use serde_json::{json, Value};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const ALPN: &[u8] = b"lobby-relay-test";

struct Lobby(std::process::Child, u16);

impl Drop for Lobby {
    fn drop(&mut self) {
        let _ = self.0.kill();
    }
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

fn lobby(relay_port: u16) -> Lobby {
    let mut c = std::process::Command::new(env!("CARGO_BIN_EXE_lobby"))
        .args(["--listen", "127.0.0.1", "--port", "0", "--turn-port", "0", "--relay-port", &relay_port.to_string(), "--relay-url", &format!("http://127.0.0.1:{relay_port}/")])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().expect("lobby runs");
    let mut line = String::new();
    std::io::BufReader::new(c.stdout.take().unwrap()).read_line(&mut line).unwrap();
    let ready: Value = serde_json::from_str(&line).expect("its ready line");
    Lobby(c, ready["port"].as_u64().unwrap() as u16)
}

/// In the lobby as `key`, proved: its welcome, and its connection, kept open (the session lasts while it is).
async fn hello(port: u16, name: &str, key: &SecretKey) -> (Value, impl Sized) {
    let (mut ws, _) = connect_async(format!("ws://127.0.0.1:{port}/")).await.unwrap();
    let public = key.public().to_string();
    let h = ToLobby::Hello { proto: PROTO4.into(), name: name.into(), key: Some(public.clone()), sim: json!({}), status: "available".into(), max_visitors: 3,
                             resume: None, world: false, links: false, chat: false, whisper: false, voice: false, about: None };
    ws.send(Message::Text(h.to_json().to_string().into())).await.unwrap();
    macro_rules! next {
        () => {
            loop {
                if let Some(Ok(Message::Text(t))) = ws.next().await {
                    break serde_json::from_str::<Value>(t.as_str()).unwrap();
                }
            }
        };
    }
    let ch = next!();
    assert_eq!(ch["type"], "challenge");
    let sig = key.sign(proof_message(&format!("127.0.0.1:{port}"), ch["nonce"].as_str().unwrap(), &public).as_bytes());
    let hex: String = sig.to_bytes().iter().map(|b| format!("{b:02x}")).collect();
    ws.send(Message::Text(ToLobby::Prove { signature: hex }.to_json().to_string().into())).await.unwrap();
    let w = next!();
    assert_eq!(w["type"], "welcome", "{w}");
    (w, ws)
}

/// An endpoint that reaches others only through `relay`: no IP transport of its own, so no direct path.
async fn endpoint(key: SecretKey, relay: &RelayUrl) -> Endpoint {
    Endpoint::builder(presets::Minimal).secret_key(key).alpns(vec![ALPN.to_vec()]).relay_mode(RelayMode::custom([relay.clone()]))
        .clear_ip_transports().bind().await.unwrap()
}

#[tokio::test]
async fn offices_in_the_lobby_link_through_its_relay_and_nobody_else_uses_it() {
    let relay_port = free_port();
    let l = lobby(relay_port);
    let url: RelayUrl = format!("http://127.0.0.1:{relay_port}/").parse().unwrap();
    let (ka, kb, kc) = (SecretKey::generate(), SecretKey::generate(), SecretKey::generate());
    let (wa, _a) = hello(l.1, "Relay A", &ka).await;
    let (_wb, _b) = hello(l.1, "Relay B", &kb).await;
    assert_eq!(wa["relays"], json!([url.to_string()]), "the welcome names the lobby's relay");

    let (a, b) = (endpoint(ka, &url).await, endpoint(kb, &url).await);
    tokio::time::timeout(Duration::from_secs(10), a.online()).await.expect("A, in the lobby, is on its relay");
    tokio::time::timeout(Duration::from_secs(10), b.online()).await.expect("B too");
    let host = a.clone();
    let echo = tokio::spawn(async move {
        let conn = host.accept().await.unwrap().accept().unwrap().await.unwrap();
        let (mut send, mut recv) = conn.accept_bi().await.unwrap();
        let got = recv.read_to_end(1000).await.unwrap();
        send.write_all(&got).await.unwrap();
        send.finish().unwrap();
        conn.closed().await;
    });
    let to_a = EndpointAddr::new(a.id()).with_relay_url(url.clone());
    let conn = tokio::time::timeout(Duration::from_secs(15), b.connect(to_a, ALPN)).await.expect("B reaches A in time").expect("B reaches A");
    let (mut send, mut recv) = conn.open_bi().await.unwrap();
    send.write_all(b"through the lobby's relay").await.unwrap();
    send.finish().unwrap();
    assert_eq!(recv.read_to_end(1000).await.unwrap(), b"through the lobby's relay");
    conn.close(0u32.into(), b"done");
    let _ = tokio::time::timeout(Duration::from_secs(2), echo).await;

    let c = endpoint(kc, &url).await;   // a key the lobby has never seen
    assert!(tokio::time::timeout(Duration::from_secs(4), c.online()).await.is_err(), "a key not in the lobby is not let onto its relay");
}

#[tokio::test]
async fn the_relay_answers_quic_address_discovery_given_a_certificate() {
    let dir = std::env::temp_dir().join(format!("lobby-relay-cert-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let cert = rcgen::generate_simple_self_signed(vec!["localhost".into()]).unwrap();
    std::fs::write(dir.join("cert.pem"), cert.cert.pem()).unwrap();
    std::fs::write(dir.join("key.pem"), cert.signing_key.serialize_pem()).unwrap();
    let (relay_port, quic_port) = (free_port(), free_port());
    let mut c = std::process::Command::new(env!("CARGO_BIN_EXE_lobby"))
        .args(["--listen", "127.0.0.1", "--port", "0", "--turn-port", "0", "--relay-port", &relay_port.to_string(), "--relay-url", &format!("http://127.0.0.1:{relay_port}/"),
               "--relay-quic-port", &quic_port.to_string()])
        .arg("--relay-cert").arg(dir.join("cert.pem")).arg("--relay-key").arg(dir.join("key.pem"))
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let mut line = String::new();
    std::io::BufReader::new(c.stdout.take().unwrap()).read_line(&mut line).unwrap();
    assert!(line.contains("\"ready\":true"), "the lobby starts with its relay's certificate: {line}");
    let taken = std::net::UdpSocket::bind(("0.0.0.0", quic_port)).is_err();
    let _ = c.kill();
    assert!(taken, "its QUIC address discovery listens on UDP {quic_port}");
    let _ = std::fs::remove_dir_all(dir);
}
