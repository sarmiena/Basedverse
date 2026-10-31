//! The lobby's own iroh relay (../../LOBBY.md 1): for links between visit-clients that cannot reach each other
//! directly, in place of iroh's public relays. It carries their QUIC packets end to end encrypted: it cannot read them.
//! Only offices in this lobby use it (version 4: their keys proved at hello); anyone else is turned away.
//!
//! It answers HTTP on its own port, behind the lobby's TLS proxy (deploy/Caddyfile routes /relay, /ping and
//! /generate_204 to it), and, given a certificate, QUIC address discovery on UDP 7842: how a visit-client learns its
//! public address, so two can punch through their NATs and need the relay only when that fails.
use std::{net::SocketAddr, num::NonZeroU32, path::PathBuf, sync::Arc};

use anyhow::Context;
use iroh_relay::server::{Access, AccessControl, ClientRateLimit, ClientRequest, Limits, QuicConfig, RelayConfig, Server, ServerConfig};
use tracing::info;

use crate::core::{Ev, Handle};

/// Bytes a second, and in a burst, one client may send through the relay (a fallback for links: a room's scene,
/// voice, pictures).
const RATE: u32 = 4 << 20;
const BURST: u32 = 16 << 20;

pub struct Settings {
    /// Where its HTTP answers (behind the TLS proxy): 127.0.0.1:<port>.
    pub http: SocketAddr,
    /// QUIC address discovery: its UDP address and the certificate (PEM chain, PEM key) of the relay's name, read
    /// again every hour (a certificate renewed by the proxy is taken up by itself).
    pub quic: Option<(SocketAddr, PathBuf, PathBuf)>,
}

/// Offices in the lobby, by the key their relay connection proves (the relay's handshake proves it, as the lobby's
/// hello does).
struct InTheLobby(Handle);

impl std::fmt::Debug for InTheLobby {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("InTheLobby")
    }
}

impl AccessControl for InTheLobby {
    async fn on_connect(&self, request: &ClientRequest) -> Access {
        let key = request.endpoint_id().to_string();
        match self.0.ask(|r| Ev::RelayAccess(key, r)).await {
            Some(true) => Access::Allow,
            _ => Access::Deny { reason: Some("this relay is for offices in its lobby".into()) },
        }
    }
}

pub async fn start(s: Settings, core: Handle) -> anyhow::Result<Server> {
    let mut relay = RelayConfig::new(s.http);
    let mut limits = Limits::default();
    let mut rate = ClientRateLimit::new(NonZeroU32::new(RATE).expect("not zero"));
    rate.max_burst_bytes = NonZeroU32::new(BURST);
    limits.client_rx = Some(rate);
    relay.limits = limits;
    relay.access = Arc::new(InTheLobby(core));
    let mut cfg = ServerConfig::default();
    cfg.relay = Some(relay);
    if let Some((at, cert, key)) = &s.quic {
        let provider = rustls::crypto::ring::default_provider();
        let resolver = iroh_relay::server::reloading_resolver(&provider, cert.clone(), key.clone(), std::time::Duration::from_secs(3600)).await
            .map_err(|e| anyhow::anyhow!("{e}")).with_context(|| format!("the relay's certificate ({}, {})", cert.display(), key.display()))?;
        let tls = rustls::ServerConfig::builder_with_provider(Arc::new(provider))
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .with_no_client_auth()
            .with_cert_resolver(resolver);
        let mut quic = QuicConfig::new(*at);
        quic.server_config = Some(tls);
        cfg.quic = Some(quic);
    }
    let server = Server::spawn(cfg).await.map_err(|e| anyhow::anyhow!("{e}"))?;
    info!(http = ?server.http_addr(), quic = ?server.quic_addr(), "the relay: for offices in this lobby");
    Ok(server)
}
