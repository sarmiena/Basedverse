//! The lobby (../LOBBY.md, ../../visit/SPEC.md 6): who is online, knocks, visits and links between visit-clients,
//! world groups, the lobby's chat and whispers. One program for anyone to host: plain HTTP and WebSockets on one
//! port, with a TLS proxy in front of it on the internet (Caddy: deploy/). What offices show each other never passes
//! through here.
//!
//!   lobby                          listens on 0.0.0.0:8765 (LOBBY_LISTEN, LOBBY_PORT)
//!   lobby --data-dir /var/lib/lobby --admin <key> --title "Our lobby"
mod conn;
mod core;
mod groups;
mod relay;
mod web;

use std::{
    collections::{HashMap, HashSet},
    net::{IpAddr, SocketAddr},
    path::PathBuf,
    process::ExitCode,
    sync::{atomic::AtomicU64, Arc, Mutex},
    time::Duration,
};

use clap::Parser;
use tokio::sync::mpsc;
use tracing::{error, info, warn};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Parser)]
#[command(name = "lobby", version, about = "The lobby: where visit-clients meet (LOBBY.md)")]
struct Args {
    /// The address to listen on.
    #[arg(long, env = "LOBBY_LISTEN", default_value = "0.0.0.0")]
    listen: IpAddr,
    /// The port: HTTP and WebSockets (a TLS proxy in front of it on the internet).
    #[arg(long, env = "LOBBY_PORT", default_value_t = 8765)]
    port: u16,
    /// Where it keeps its world groups and bans (groups.json, bans.json). None: in memory, gone at a restart.
    #[arg(long, env = "LOBBY_DATA_DIR")]
    data_dir: Option<PathBuf>,
    /// The lobby's operators: keys, comma-separated (an office's key: its panel shows it). They ban keys and run
    /// every group.
    #[arg(long, env = "LOBBY_ADMINS", value_delimiter = ',')]
    admin: Vec<String>,
    /// The lobby's name, for its page and its welcome.
    #[arg(long, env = "LOBBY_TITLE")]
    title: Option<String>,
    /// A message for everyone joining (its welcome), and its page.
    #[arg(long, env = "LOBBY_MOTD")]
    motd: Option<String>,
    /// The authorities this lobby is reached at (host, or host:port; 80 and 443 left out), comma-separated: what a
    /// version-4 hello must have signed. None: the Host each connection came with.
    #[arg(long, env = "LOBBY_HOSTS", value_delimiter = ',')]
    hosts: Vec<String>,
    /// iroh relay URLs to give visit-clients, comma-separated. None: their own (iroh's), unless --relay-port runs one.
    #[arg(long, env = "LOBBY_RELAYS", value_delimiter = ',')]
    relays: Vec<String>,
    /// Run the lobby's own iroh relay (for its offices only) on this port, beside the lobby, behind the TLS proxy
    /// (which routes /relay, /ping and /generate_204 to it). Visit-clients are told to use it.
    #[arg(long, env = "LOBBY_RELAY_PORT")]
    relay_port: Option<u16>,
    /// The relay's URL, as visit-clients reach it. Default: https://<the first of --hosts>/.
    #[arg(long, env = "LOBBY_RELAY_URL")]
    relay_url: Option<String>,
    /// The relay's certificate and key (PEM files, read again every hour), for its QUIC address discovery on UDP
    /// --relay-quic-port: how visit-clients learn their public address, to reach each other directly.
    #[arg(long, env = "LOBBY_RELAY_CERT", requires = "relay_key")]
    relay_cert: Option<PathBuf>,
    #[arg(long, env = "LOBBY_RELAY_KEY", requires = "relay_cert")]
    relay_key: Option<PathBuf>,
    #[arg(long, env = "LOBBY_RELAY_QUIC_PORT", default_value_t = 7842)]
    relay_quic_port: u16,
    /// Take a connection's address from X-Forwarded-For (its last entry): only behind a proxy that sets it.
    #[arg(long, env = "LOBBY_TRUST_PROXY", action = clap::ArgAction::SetTrue, value_parser = clap::builder::FalseyValueParser::new())]
    trust_proxy: bool,
    /// Also speak basedverse-lobby/3 (Visit 0.5 to 2.x), a lobby of its own: for carrying old workrooms through
    /// the upgrade. Its offices prove no key and need no sign-in.
    #[arg(long, env = "LOBBY_V3", action = clap::ArgAction::SetTrue, value_parser = clap::builder::FalseyValueParser::new())]
    v3: bool,
    /// Seconds a knock waits for its answer.
    #[arg(long, env = "LOBBY_KNOCK_TTL", default_value_t = 60)]
    knock_ttl: u64,
    /// Seconds a dropped session is kept for its visit-client to resume (a test aid; the protocol says 30).
    #[arg(long, env = "LOBBY_RESUME", default_value_t = 30, hide = true)]
    resume: u64,
    /// Seconds between pings; a connection that leaves one unanswered as long is dropped.
    #[arg(long, env = "LOBBY_PING", default_value_t = 20)]
    ping: u64,
    /// No public page at / (the WebSocket and /health only).
    #[arg(long, env = "LOBBY_NO_PAGE", action = clap::ArgAction::SetTrue, value_parser = clap::builder::FalseyValueParser::new())]
    no_page: bool,
    /// A test aid, never for a lobby people use: /debug/state, /debug/drop/<office>, /debug/reset, /debug/lie.
    #[arg(long, env = "LOBBY_DEBUG", action = clap::ArgAction::SetTrue, value_parser = clap::builder::FalseyValueParser::new())]
    debug: bool,
}

/// What every connection shares.
pub struct ServerCfg {
    pub hosts: Vec<String>,
    pub trust_proxy: bool,
    pub v3: bool,
    pub ping: Duration,
    pub page: bool,
    pub debug: bool,
    pub title: Option<String>,
    pub motd: Option<String>,
}

pub struct Server {
    pub cfg: ServerCfg,
    pub core: core::Handle,
    per_ip: Mutex<HashMap<IpAddr, usize>>,
    conns: AtomicU64,
    /// What the public page last read, and when: read again at most every 2 s, whoever asks.
    pub page_cache: Mutex<Option<(std::time::Instant, serde_json::Value)>>,
}

impl Server {
    pub fn next_conn(&self) -> u64 {
        self.conns.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    }

    /// One more connection from `ip`, if it has fewer than `connections_per_ip`.
    pub fn arrived(&self, ip: IpAddr) -> bool {
        let mut m = self.per_ip.lock().expect("per ip");
        let n = m.entry(ip).or_insert(0);
        if *n >= lobby_proto::Limits::v4(0).connections_per_ip {
            return false;
        }
        *n += 1;
        true
    }

    pub fn left(&self, ip: IpAddr) {
        let mut m = self.per_ip.lock().expect("per ip");
        if let Some(n) = m.get_mut(&ip) {
            *n -= 1;
            if *n == 0 {
                m.remove(&ip);
            }
        }
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt().with_writer(std::io::stderr)
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "lobby=info".into())).init();
    let mut a = Args::parse();
    // A setting given blank is no setting (an env file's LOBBY_MOTD= says none).
    for v in [&mut a.title, &mut a.motd, &mut a.relay_url] {
        if v.as_deref().is_some_and(|s| s.trim().is_empty()) {
            *v = None;
        }
    }
    let bad = |m: String| {
        eprintln!("lobby: {m}");
        ExitCode::from(2)
    };
    let admins: HashSet<String> = a.admin.iter().map(|k| k.trim().to_ascii_lowercase()).filter(|k| !k.is_empty()).collect();
    if let Some(k) = admins.iter().find(|k| !lobby_proto::key_ok(k)) {
        return bad(format!("--admin {k}: a key is 64 hex characters (an office's key, as its panel shows it)"));
    }
    if a.ping == 0 || a.knock_ttl == 0 {
        return bad("--ping and --knock-ttl are at least 1".into());
    }
    if let Some(d) = &a.data_dir {
        if let Err(e) = std::fs::create_dir_all(d) {
            return bad(format!("--data-dir {}: {e}", d.display()));
        }
    }
    let (groups, bans) = match (groups::Groups::open(a.data_dir.as_deref()), groups::Bans::open(a.data_dir.as_deref())) {
        (Ok(g), Ok(b)) => (g, b),
        (Err(e), _) | (_, Err(e)) => {
            eprintln!("lobby: {e:#}");
            return ExitCode::from(5);
        }
    };
    let hosts: Vec<String> = a.hosts.iter().map(|h| lobby_proto::authority(h.trim())).filter(|h| !h.is_empty()).collect();
    let mut relays: Vec<String> = a.relays.iter().map(|r| r.trim().to_string()).filter(|r| !r.is_empty()).collect();
    if a.relay_port.is_some() {
        match a.relay_url.clone().or_else(|| hosts.first().map(|h| format!("https://{h}/"))) {
            Some(u) => relays.insert(0, u),
            None => return bad("--relay-port: say how visit-clients reach it (--relay-url), or the lobby's name (--hosts)".into()),
        }
    }
    if a.relay_cert.is_some() && a.relay_port.is_none() {
        return bad("--relay-cert: for the relay (--relay-port)".into());
    }
    let (tx, rx) = mpsc::unbounded_channel();
    let cfg = core::Config { knock_ttl: a.knock_ttl, resume: a.resume, relays, admins, title: a.title.clone(), motd: a.motd.clone() };
    tokio::spawn(core::Core::new(cfg, tx.clone(), bans, groups).run(rx));
    let _relay = match a.relay_port {
        Some(port) => {
            let quic = a.relay_cert.clone().zip(a.relay_key.clone()).map(|(c, k)| ((std::net::Ipv4Addr::UNSPECIFIED, a.relay_quic_port).into(), c, k));
            match relay::start(relay::Settings { http: (a.listen, port).into(), quic }, core::Handle(tx.clone())).await {
                Ok(r) => Some(r),
                Err(e) => {
                    eprintln!("lobby: the relay: {e:#}");
                    return ExitCode::from(5);
                }
            }
        }
        None => None,
    };
    let server = Arc::new(Server {
        cfg: ServerCfg {
            hosts, trust_proxy: a.trust_proxy,
            v3: a.v3, ping: Duration::from_secs(a.ping), page: !a.no_page, debug: a.debug, title: a.title, motd: a.motd,
        },
        core: core::Handle(tx), per_ip: Mutex::new(HashMap::new()), conns: AtomicU64::new(1), page_cache: Mutex::new(None),
    });
    let listener = match tokio::net::TcpListener::bind((a.listen, a.port)).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("lobby: {}:{}: {e}", a.listen, a.port);
            return ExitCode::from(3);
        }
    };
    let port = listener.local_addr().map_or(a.port, |s| s.port());
    info!(version = VERSION, v3 = server.cfg.v3, "{} listening on {}:{port}", lobby_proto::PROTO4, a.listen);
    if server.cfg.hosts.is_empty() {
        warn!("no --hosts: version-4 hellos are taken only for this machine's own names (127.0.0.1, localhost); give the names this lobby is reached by");
    }
    println!("{}", serde_json::json!({ "ready": true, "port": port, "version": VERSION, "proto": lobby_proto::PROTO4 }));
    let app = web::router(server.clone()).into_make_service_with_connect_info::<SocketAddr>();
    // Stopping drops every connection at once: their visit-clients reconnect to the lobby when it is back (an empty
    // one: what offices say is kept nowhere, and their sessions with it).
    tokio::select! {
        r = axum::serve(listener, app) => {
            if let Err(e) = r {
                error!("the server stopped: {e}");
                return ExitCode::FAILURE;
            }
        }
        _ = stopped() => info!("stopping"),
    }
    ExitCode::SUCCESS
}

/// Ctrl-C, or SIGTERM (what systemd sends).
async fn stopped() {
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = term.recv() => {}
        }
    }
    #[cfg(not(unix))]
    let _ = tokio::signal::ctrl_c().await;
}
