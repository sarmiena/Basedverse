//! Pictures and voice messages in chat (../../../visit/SPEC.md 8.12), as both sides check them: what one may be, its
//! id (the blake3 hash of its bytes), and what a line may carry. The lobby passes a world's pictures on (5.3, 6.6), so
//! it checks them as a visit-client does.
use serde_json::{json, Value};

/// The largest picture shared (a screenshot fits).
pub const MAX_MEDIA: usize = 8 << 20;
/// The largest voice message (Visit 0.22.0): a minute, recorded as a browser does (Opus, else AAC), fits.
pub const MAX_VOICE: usize = 2 << 20;
/// A voice message's longest, in seconds, as its sender says.
pub const VOICE_SECS: f64 = 60.0;
/// At most this many pictures (a voice message among them, at most one), and links, in a line.
pub const MEDIA_PER_LINE: usize = 4;
pub const LINKS_PER_LINE: usize = 2;
/// The kinds a picture may be, and a voice message.
pub const PICTURE_KINDS: [&str; 4] = ["image/png", "image/jpeg", "image/webp", "image/gif"];
pub const VOICE_KINDS: [&str; 3] = ["audio/webm", "audio/ogg", "audio/mp4"];
/// 5.3: the most of a picture in one message to the lobby (its frame, 64 KiB, with room to spare), and from it.
pub const PIECE_UP: usize = 60 << 10;
pub const PIECE_DOWN: usize = 256 << 10;

/// A picture's type, by what its bytes start with (whatever it was said to be): PNG, JPEG, WebP or GIF; else none.
pub fn sniff(b: &[u8]) -> Option<&'static str> {
    if b.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some("image/png")
    } else if b.starts_with(&[0xFF, 0xD8, 0xFF]) {
        Some("image/jpeg")
    } else if b.starts_with(b"GIF87a") || b.starts_with(b"GIF89a") {
        Some("image/gif")
    } else if b.len() >= 12 && &b[..4] == b"RIFF" && &b[8..12] == b"WEBP" {
        Some("image/webp")
    } else {
        None
    }
}

/// A voice message's type, by what its bytes start with: WebM (Chrome's and Firefox's recordings), Ogg, or MP4
/// (Safari's); else none.
pub fn sniff_voice(b: &[u8]) -> Option<&'static str> {
    if b.starts_with(&[0x1A, 0x45, 0xDF, 0xA3]) && b[..b.len().min(64)].windows(4).any(|w| w == b"webm") {
        Some("audio/webm")
    } else if b.starts_with(b"OggS") {
        Some("audio/ogg")
    } else if b.len() >= 12 && &b[4..8] == b"ftyp" {
        Some("audio/mp4")
    } else {
        None
    }
}

/// An id (64 lowercase hex characters) as the hash of these bytes.
pub fn id_of(b: &[u8]) -> String {
    blake3::hash(b).to_hex().to_string()
}

pub fn id_ok(id: &str) -> bool {
    id.len() == 64 && id.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// The most a thing of this kind may be: a picture's, a voice message's; none, not a kind a line carries.
pub fn max_of(mime: &str) -> Option<usize> {
    if PICTURE_KINDS.contains(&mime) {
        Some(MAX_MEDIA)
    } else if VOICE_KINDS.contains(&mime) {
        Some(MAX_VOICE)
    } else {
        None
    }
}

/// Whether these bytes are what a line names as `id` and `mime`: they hash to it, are of that kind by what they start
/// with, and are no bigger than that kind may be.
pub fn is(id: &str, mime: &str, b: &[u8]) -> bool {
    let kind = sniff(b).or_else(|| sniff_voice(b));
    !b.is_empty() && kind == Some(mime) && max_of(mime).is_some_and(|max| b.len() <= max) && id_of(b) == id
}

/// A link that may be shared: http or https, at most 2 000 characters, no spaces or control characters.
pub fn url_ok(u: &str) -> bool {
    (u.starts_with("http://") || u.starts_with("https://")) && u.len() <= 2000 && u.len() > 8 && !u.chars().any(|c| c.is_whitespace() || c.is_control())
}

/// At most `n` characters, without control characters, trimmed.
pub fn clip(s: &str, n: usize) -> String {
    s.chars().map(|c| if c.is_control() { ' ' } else { c }).collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ").chars().take(n).collect()
}

/// The pictures and links a line carries, as a page, a link or the lobby gives them, checked (at most MEDIA_PER_LINE
/// and LINKS_PER_LINE): each picture { id, mime, size }, or one voice message { id, mime, size, secs }; each link { url
/// (http or https), title, description, site, image (a picture) }.
pub fn attachments(m: &Value) -> (Vec<Value>, Vec<Value>) {
    let pic = |p: &Value| -> Option<Value> {
        let id = p.get("id").and_then(Value::as_str).filter(|id| id_ok(id))?;
        let mime = p.get("mime").and_then(Value::as_str).filter(|m| PICTURE_KINDS.contains(m))?;
        let size = p.get("size").and_then(Value::as_u64).filter(|s| *s > 0 && *s as usize <= MAX_MEDIA)?;
        Some(json!({ "id": id, "mime": mime, "size": size }))
    };
    let voice = |p: &Value| -> Option<Value> {
        let id = p.get("id").and_then(Value::as_str).filter(|id| id_ok(id))?;
        let mime = p.get("mime").and_then(Value::as_str).filter(|m| VOICE_KINDS.contains(m))?;
        let size = p.get("size").and_then(Value::as_u64).filter(|s| *s > 0 && *s as usize <= MAX_VOICE)?;
        let secs = p.get("secs").and_then(Value::as_f64).filter(|s| s.is_finite()).map_or(0.0, |s| (s.clamp(0.0, VOICE_SECS) * 10.0).round() / 10.0);
        Some(json!({ "id": id, "mime": mime, "size": size, "secs": secs }))
    };
    let mut voices = 0;
    let media = m.get("media").and_then(Value::as_array).into_iter().flatten().take(MEDIA_PER_LINE)
        .filter_map(|p| pic(p).or_else(|| voice(p).filter(|_| { voices += 1; voices == 1 }))).collect();
    let links = m.get("links").and_then(Value::as_array).into_iter().flatten().take(LINKS_PER_LINE).filter_map(|l| {
        let url = l.get("url").and_then(Value::as_str).filter(|u| url_ok(u))?;
        let s = |k: &str, n: usize| clip(l.get(k).and_then(Value::as_str).unwrap_or(""), n);
        Some(json!({ "url": url, "title": s("title", 200), "description": s("description", 500), "site": s("site", 100),
                     "image": l.get("image").and_then(pic).unwrap_or(Value::Null) }))
    }).collect();
    (media, links)
}

/// The pictures a line names (`attachments`' output): its own, and its links' previews'. Each once.
pub fn ids(media: &[Value], links: &[Value]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for p in media.iter().chain(links.iter().map(|l| &l["image"])) {
        if let (Some(id), Some(mime)) = (p["id"].as_str(), p["mime"].as_str()) {
            if !out.iter().any(|(i, _)| i == id) {
                out.push((id.to_string(), mime.to_string()));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checked() {
        let png = b"\x89PNG\r\n\x1a\nrest of it".to_vec();
        let id = id_of(&png);
        assert!(is(&id, "image/png", &png));
        assert!(!is(&id, "image/jpeg", &png), "said to be another kind");
        assert!(!is(&id_of(b"other"), "image/png", &png), "another's id");
        assert!(!is(&id_of(b"text"), "text/plain", b"text"), "not a kind a line carries");
        let ogg = b"OggS and the rest".to_vec();
        assert!(is(&id_of(&ogg), "audio/ogg", &ogg));
        let big = [b"\x89PNG\r\n\x1a\n".as_slice(), &vec![0u8; MAX_MEDIA]].concat();
        assert!(!is(&id_of(&big), "image/png", &big), "past 8 MiB");
    }

    #[test]
    fn a_lines_pictures() {
        let a = "a".repeat(64);
        let b = "b".repeat(64);
        let m = json!({
            "media": [{ "id": a, "mime": "image/png", "size": 10 }, { "id": "short", "mime": "image/png", "size": 1 },
                      { "id": b, "mime": "audio/ogg", "size": 5, "secs": 99 }, { "id": b, "mime": "audio/webm", "size": 5 }],
            "links": [{ "url": "https://example.com/x", "title": "X\u{7}", "image": { "id": a, "mime": "image/png", "size": 10 } }, { "url": "ftp://x" }],
        });
        let (media, links) = attachments(&m);
        assert_eq!(media.len(), 2, "a bad id, and a second voice message, left out");
        assert_eq!(media[1]["secs"], json!(60.0));
        assert_eq!(links.len(), 1);
        assert_eq!(links[0]["title"], json!("X"));
        assert_eq!(ids(&media, &links), vec![(a.clone(), "image/png".into()), (b, "audio/ogg".into())], "each once");
    }
}
