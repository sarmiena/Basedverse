//! The lobby's STUN and TURN server (../../LOBBY.md 1; Visit's voice, ../../visit/SPEC.md 8.7, #71): for the pages of its
//! offices to reach each office's WebRTC forwarder. STUN tells a page (and an office) its address as seen from
//! outside; TURN carries a page's voice when no direct way goes (a phone's mobile network, a strict NAT). Only the
//! lobby's offices may relay: each gets a login of its own in its welcome, good while it is in the lobby (and a
//! while after, for calls going on), never on a timer that runs out under a long call.
use std::{
    collections::HashMap,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use anyhow::{anyhow, Result};
use tokio::net::UdpSocket;
use tracing::info;
use turn::{
    auth::{generate_auth_key, AuthHandler},
    relay::relay_range::RelayAddressGeneratorRanges,
    server::{
        config::{ConnConfig, ServerConfig},
        Server,
    },
};
use webrtc_util::vnet::net::Net;

pub const REALM: &str = "basedverse";
/// A login stays good this long after its office leaves (its pages' calls end, their relays expire).
const AFTER_LEAVING: Duration = Duration::from_secs(600);
/// The most authenticated requests one login makes a minute (an allocation, a refresh, a permission each: a browser
/// allocates a relay for each of its network interfaces on each connection); past it, refused until the minute is up.
const ASKS_PER_MINUTE: u32 = 600;

struct User {
    key: Vec<u8>,
    gone_at: Option<Instant>,
    asks: (Instant, u32),
}

/// The logins: one per office in the lobby.
#[derive(Default)]
pub struct Users(Mutex<HashMap<String, User>>);

impl Users {
    /// A new login for an office (a session): its username and password.
    pub fn issue(&self, office: &str) -> (String, String) {
        let username = format!("{office}.{}", crate::core::hex(6));
        let password = crate::core::hex(16);
        let mut map = self.0.lock().expect("turn users");
        map.retain(|_, u| u.gone_at.is_none_or(|t| t.elapsed() < AFTER_LEAVING));
        map.insert(username.clone(), User { key: generate_auth_key(&username, REALM, &password), gone_at: None, asks: (Instant::now(), 0) });
        (username, password)
    }

    /// Its office left: good a while longer.
    pub fn left(&self, username: &str) {
        if let Some(u) = self.0.lock().expect("turn users").get_mut(username) {
            u.gone_at.get_or_insert_with(Instant::now);
        }
    }
}

struct Auth(Arc<Users>);

impl AuthHandler for Auth {
    fn auth_handle(&self, username: &str, _realm: &str, _src: SocketAddr) -> Result<Vec<u8>, turn::Error> {
        let mut map = self.0 .0.lock().expect("turn users");
        let u = map.get_mut(username).ok_or(turn::Error::ErrFakeErr)?;
        if u.gone_at.is_some_and(|t| t.elapsed() >= AFTER_LEAVING) {
            return Err(turn::Error::ErrFakeErr);
        }
        if u.asks.0.elapsed() >= Duration::from_secs(60) {
            u.asks = (Instant::now(), 0);
        }
        u.asks.1 += 1;
        if u.asks.1 > ASKS_PER_MINUTE {
            return Err(turn::Error::Other("too many requests".into()));
        }
        Ok(u.key.clone())
    }
}

pub struct Settings {
    /// Its UDP port (STUN and TURN).
    pub port: u16,
    /// The public address relays are given at; none: STUN only (no login works).
    pub public_ip: Option<IpAddr>,
    /// The ports relays are made on (each relayed connection takes one: their number is the most at once).
    pub relay_ports: (u16, u16),
}

pub struct Turn {
    pub users: Arc<Users>,
    _server: Server,
}

pub async fn start(s: Settings) -> Result<Turn> {
    let conn = Arc::new(UdpSocket::bind(SocketAddr::from((Ipv4Addr::UNSPECIFIED, s.port))).await?);
    let users = Arc::new(Users::default());
    let server = Server::new(ServerConfig {
        conn_configs: vec![ConnConfig {
            conn,
            relay_addr_generator: Box::new(RelayAddressGeneratorRanges {
                relay_address: s.public_ip.unwrap_or(IpAddr::V4(Ipv4Addr::LOCALHOST)),
                min_port: s.relay_ports.0,
                max_port: s.relay_ports.1,
                max_retries: 20,
                address: "0.0.0.0".into(),
                net: Arc::new(Net::new(None)),
            }),
        }],
        realm: REALM.into(),
        auth_handler: Arc::new(Auth(users.clone())),
        channel_bind_timeout: Duration::from_secs(0),
        alloc_close_notify: None,
    })
    .await
    .map_err(|e| anyhow!("the TURN server: {e}"))?;
    match s.public_ip {
        Some(ip) => info!("STUN and TURN on UDP {}, relays at {ip}, ports {}-{}", s.port, s.relay_ports.0, s.relay_ports.1),
        None => info!("STUN on UDP {} (no TURN: --turn-ip not given)", s.port),
    }
    Ok(Turn { users, _server: server })
}
