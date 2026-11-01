//! HTTP: the WebSocket (any path, as the lobby always took it), `/health`, the public page at `/` and what it reads
//! (`/api/lobby`), and the test aid's `/debug/…` (--debug only).
use std::{net::{IpAddr, SocketAddr}, sync::Arc};

use axum::{
    body::Bytes,
    extract::{ws::WebSocketUpgrade, ConnectInfo, Path, State},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use lobby_proto::FromLobby;
use serde_json::json;

use crate::{core::Ev, Server};

pub fn router(server: Arc<Server>) -> Router {
    let mut r = Router::new().route("/health", get(health));
    if server.cfg.debug {
        r = r.route("/debug/state", get(debug_state))
            .route("/debug/drop/{office}", post(debug_drop))
            .route("/debug/reset", post(debug_reset))
            .route("/debug/lie", post(debug_lie));
    }
    r.fallback(any).with_state(server)
}

/// The address a connection is from: its own, or, behind a proxy that says (--trust-proxy), the last address of its
/// X-Forwarded-For (the one the proxy added: the ones before it are the client's to make up).
fn client_ip(s: &Server, peer: SocketAddr, h: &HeaderMap) -> IpAddr {
    if s.cfg.trust_proxy {
        if let Some(ip) = h.get("x-forwarded-for").and_then(|v| v.to_str().ok()).and_then(|v| v.rsplit(',').next()).and_then(|v| v.trim().parse().ok()) {
            return ip;
        }
    }
    peer.ip()
}

async fn any(State(s): State<Arc<Server>>, ConnectInfo(peer): ConnectInfo<SocketAddr>, h: HeaderMap, uri: axum::http::Uri,
             ws: Result<WebSocketUpgrade, axum::extract::ws::rejection::WebSocketUpgradeRejection>) -> Response {
    match ws {
        Ok(ws) => {
            let ip = client_ip(&s, peer, &h);
            let host = h.get(header::HOST).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
            let agent = agent_label(h.get(header::USER_AGENT).and_then(|v| v.to_str().ok()).unwrap_or(""));
            let frame = lobby_proto::Limits::v4(0).frame;
            ws.max_message_size(frame).max_frame_size(frame).on_upgrade(move |mut socket| async move {
                if !s.arrived(ip) {
                    use axum::extract::ws::{CloseFrame, Message};
                    let e = FromLobby::error("too_many_connections", format!("at most {} connections from one address", lobby_proto::Limits::v4(0).connections_per_ip), None);
                    let _ = socket.send(Message::Text(e.text().into())).await;
                    let _ = socket.send(Message::Close(Some(CloseFrame { code: 1008, reason: "too many connections".into() }))).await;
                    return;
                }
                crate::conn::run(s, socket, ip, host, agent).await
            })
        }
        Err(_) if uri.path() == "/" && s.cfg.page => page(&s).into_response(),
        Err(_) if uri.path() == "/api/lobby" && s.cfg.page => api_lobby(&s).await.into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "the lobby: connect with Visit (a WebSocket), or see / in a browser\n").into_response(),
    }
}

/// A browser, as a person names it ("Firefox on Mac"), from its User-Agent: what a room shows of a browser it links.
pub fn agent_label(ua: &str) -> String {
    let has = |s: &str| ua.contains(s);
    let browser = if has("Edg/") { "Edge" } else if has("OPR/") { "Opera" } else if has("Firefox/") || has("FxiOS/") { "Firefox" }
        else if has("Chrome/") || has("CriOS/") { "Chrome" } else if has("Safari/") { "Safari" } else { "A browser" };
    let os = if has("iPhone") { "iPhone" } else if has("iPad") { "iPad" } else if has("Android") { "Android" } else if has("Windows") { "Windows" }
        else if has("CrOS") { "ChromeOS" } else if has("Mac OS X") || has("Macintosh") { "Mac" } else if has("Linux") { "Linux" } else { "" };
    if os.is_empty() { browser.to_string() } else { format!("{browser} on {os}") }
}

#[cfg(test)]
mod tests {
    #[test]
    fn agents() {
        assert_eq!(super::agent_label("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/147.0.0.0 Safari/537.36"), "Chrome on Linux");
        assert_eq!(super::agent_label("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"), "Safari on iPhone");
        assert_eq!(super::agent_label("Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:131.0) Gecko/20100101 Firefox/131.0"), "Firefox on Mac");
        assert_eq!(super::agent_label("Python/3.12 websockets/15.0.1"), "A browser");
    }
}

async fn health(State(s): State<Arc<Server>>) -> String {
    let n = s.core.ask(Ev::Count).await.unwrap_or(0);
    format!("ok {n} online\n")
}

/// The public page: one file, its script and styles inside it, nothing from elsewhere.
fn page(_s: &Server) -> Response {
    ([(header::CONTENT_SECURITY_POLICY, "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
      (header::X_CONTENT_TYPE_OPTIONS, "nosniff"), (header::REFERRER_POLICY, "no-referrer")],
     Html(include_str!("../web/index.html"))).into_response()
}

async fn api_lobby(s: &Server) -> Response {
    let kept = s.page_cache.lock().expect("cache").as_ref().filter(|(at, _)| at.elapsed() < std::time::Duration::from_secs(2)).map(|(_, v)| v.clone());
    let v = match kept {
        Some(v) => v,
        None => {
            let v = s.core.ask(Ev::Public).await.unwrap_or_else(|| json!({}));
            *s.page_cache.lock().expect("cache") = Some((std::time::Instant::now(), v.clone()));
            v
        }
    };
    ([(header::CACHE_CONTROL, "no-store"), (header::X_CONTENT_TYPE_OPTIONS, "nosniff")],
     Json(json!({ "title": s.cfg.title, "motd": s.cfg.motd, "version": crate::VERSION, "proto": lobby_proto::PROTO4, "lobby": v }))).into_response()
}

async fn debug_state(State(s): State<Arc<Server>>) -> Json<serde_json::Value> {
    Json(s.core.ask(Ev::DebugState).await.unwrap_or_default())
}

async fn debug_drop(State(s): State<Arc<Server>>, Path(office): Path<String>) -> StatusCode {
    match s.core.ask(|r| Ev::DebugDrop(office, r)).await {
        Some(true) => StatusCode::OK,
        _ => StatusCode::NOT_FOUND,
    }
}

async fn debug_reset(State(s): State<Arc<Server>>) -> StatusCode {
    s.core.ask(Ev::DebugReset).await;
    StatusCode::OK
}

/// Knocks say this key is their knocker's (the body; empty: they tell the truth again).
async fn debug_lie(State(s): State<Arc<Server>>, body: Bytes) -> StatusCode {
    let key = String::from_utf8_lossy(&body).trim().to_string();
    let _ = s.core.0.send(Ev::DebugLie((!key.is_empty()).then_some(key)));
    StatusCode::OK
}
