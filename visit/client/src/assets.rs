//! A scene's assets (../SPEC.md 8.14, 4.1, #67): its big pictures and meshes, each sent once and kept by the hash of
//! what it is (SHA-256, hex), so that a room's scene names them instead of carrying them, and a visitor's page fetches
//! those it lacks, a piece at a time, at its own pace. Kept on disk (a directory of its own) when there is one, else
//! for this run only. Which of them this office's own room has (and so gives to its visitors) is the core's to know:
//! here are only the bytes, whoever's they are, each checked against its name when it comes.

use std::path::{Path, PathBuf};

use bytes::Bytes;
use sha2::{Digest, Sha256};
use tracing::warn;

/// The most one asset takes.
pub const MAX_ASSET: usize = 16 << 20;
/// The most of one sent to a page at a time: a page asks for each piece (so a relay before it never holds more than
/// a few of them: engine/SPEC.md 6.3's 1 MiB).
pub const PIECE: usize = 256 << 10;
/// The most kept on disk (the least lately used go first), and in memory without a disk.
const DISK_BYTES: u64 = 1 << 30;
const MEMORY_BYTES: usize = 256 << 20;

/// An asset's name: the SHA-256 of its bytes, hex, lower case.
pub fn id_of(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

/// A name as this office writes them: 64 hex digits, lower case.
pub fn id_ok(id: &str) -> bool {
    id.len() == 64 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// What an asset may be, by its first bytes: a picture (PNG, JPEG, WebP), or a mesh (gzipped JSON).
pub fn kind_of(bytes: &[u8]) -> Option<&'static str> {
    match bytes {
        [0x89, b'P', b'N', b'G', ..] => Some("png"),
        [0xff, 0xd8, 0xff, ..] => Some("jpeg"),
        [b'R', b'I', b'F', b'F', _, _, _, _, b'W', b'E', b'B', b'P', ..] => Some("webp"),
        [0x1f, 0x8b, ..] => Some("geometry"),
        _ => None,
    }
}

/// Bytes that may be an asset: not too big, and of a kind this office keeps.
pub fn ok(bytes: &[u8]) -> bool {
    !bytes.is_empty() && bytes.len() <= MAX_ASSET && kind_of(bytes).is_some()
}

pub struct Assets {
    dir: Option<PathBuf>,
    /// Without a disk: (name, bytes), the most lately used last.
    memory: Vec<(String, Bytes)>,
    /// Bytes on disk, as last counted (trimmed past DISK_BYTES).
    disk: u64,
}

impl Assets {
    pub fn open(dir: Option<PathBuf>) -> Assets {
        let dir = dir.filter(|d| match std::fs::create_dir_all(d) {
            Ok(()) => true,
            Err(e) => { warn!(dir = %d.display(), "a scene's assets kept for this run only: {e}"); false }
        });
        let disk = dir.as_ref().map_or(0, |d| files(d).iter().map(|f| f.1).sum());
        Assets { dir, memory: Vec::new(), disk }
    }

    fn path(&self, id: &str) -> Option<PathBuf> {
        self.dir.as_ref().filter(|_| id_ok(id)).map(|d| d.join(id))
    }

    pub fn has(&self, id: &str) -> bool {
        if !id_ok(id) {
            return false;
        }
        match self.path(id) {
            Some(p) => p.is_file(),
            None => self.memory.iter().any(|(k, _)| k == id),
        }
    }

    /// The asset by its name, and it marked as lately used.
    pub fn get(&mut self, id: &str) -> Option<Bytes> {
        if !id_ok(id) {
            return None;
        }
        if let Some(p) = self.path(id) {
            let bytes = std::fs::read(&p).ok()?;
            touch(&p);
            return Some(Bytes::from(bytes));
        }
        let i = self.memory.iter().position(|(k, _)| k == id)?;
        let e = self.memory.remove(i);
        let bytes = e.1.clone();
        self.memory.push(e);
        Some(bytes)
    }

    /// One piece of it for a page: its whole length, and the PIECE (at most) from `at`. None if it is not here, or
    /// `at` is past its end.
    pub fn piece(&mut self, id: &str, at: usize) -> Option<(usize, Bytes)> {
        if let Some(p) = self.path(id) {
            use std::io::{Read, Seek, SeekFrom};
            let mut f = std::fs::File::open(&p).ok()?;
            let total = f.metadata().ok()?.len() as usize;
            if at >= total {
                return None;
            }
            let mut buf = vec![0u8; PIECE.min(total - at)];
            f.seek(SeekFrom::Start(at as u64)).ok()?;
            f.read_exact(&mut buf).ok()?;
            if at == 0 {
                touch(&p);
            }
            return Some((total, Bytes::from(buf)));
        }
        let bytes = self.get(id)?;
        (at < bytes.len()).then(|| (bytes.len(), bytes.slice(at..bytes.len().min(at + PIECE))))
    }

    /// Kept by its name, if those are its bytes (else nothing). True if it is here now.
    #[cfg(test)]
    pub fn put(&mut self, id: &str, bytes: &Bytes) -> bool {
        id_ok(id) && ok(bytes) && id_of(bytes) == id && self.put_known(id, bytes)
    }

    /// Kept by the name its bytes have (none: not an asset).
    pub fn keep(&mut self, bytes: &Bytes) -> Option<String> {
        if !ok(bytes) {
            return None;
        }
        let id = id_of(bytes);
        self.put_known(&id, bytes).then_some(id)
    }

    /// Kept by its name, its bytes already checked against it (link.rs, as they came): on disk (written beside it,
    /// then renamed), the least lately used of all let go past DISK_BYTES. True if it is here now.
    pub fn put_known(&mut self, id: &str, bytes: &Bytes) -> bool {
        if !id_ok(id) || !ok(bytes) {
            return false;
        }
        if self.has(id) {
            if let Some(p) = self.path(id) {
                touch(&p);
            }
            return true;
        }
        if let Some(d) = self.dir.clone() {
            let tmp = d.join(format!("{id}.tmp"));
            if let Err(e) = std::fs::write(&tmp, bytes).and_then(|_| std::fs::rename(&tmp, d.join(id))) {
                warn!(dir = %d.display(), "an asset could not be kept: {e}");
                return false;
            }
            self.disk += bytes.len() as u64;
            if self.disk > DISK_BYTES {
                self.disk = trim(&d, DISK_BYTES * 9 / 10);
            }
            return true;
        }
        self.memory.push((id.to_string(), bytes.clone()));
        while self.memory.iter().map(|(_, b)| b.len()).sum::<usize>() > MEMORY_BYTES && self.memory.len() > 1 {
            self.memory.remove(0);
        }
        true
    }
}

/// Marked as used now (its time is what trimming goes by).
fn touch(p: &Path) {
    if let Ok(f) = std::fs::File::options().append(true).open(p) {
        let _ = f.set_modified(std::time::SystemTime::now());
    }
}

/// The assets in a directory: (when last used, bytes, path).
fn files(d: &Path) -> Vec<(std::time::SystemTime, u64, PathBuf)> {
    std::fs::read_dir(d).into_iter().flatten().flatten()
        .filter(|e| e.file_name().to_str().is_some_and(id_ok))
        .filter_map(|e| { let m = e.metadata().ok()?; Some((m.modified().ok()?, m.len(), e.path())) })
        .collect()
}

/// The least lately used assets let go until all of them take at most `cap` bytes: what they take then.
fn trim(d: &Path, cap: u64) -> u64 {
    let mut all = files(d);
    let mut total: u64 = all.iter().map(|f| f.1).sum();
    all.sort_by_key(|f| f.0);
    for (_, len, path) in all {
        if total <= cap {
            break;
        }
        if std::fs::remove_file(&path).is_ok() {
            total -= len;
        }
    }
    total
}

#[cfg(test)]
mod tests {
    use super::*;

    fn png(n: usize) -> Bytes {
        let mut b = vec![0x89, b'P', b'N', b'G', 13, 10, 26, 10];
        b.extend((0..n).map(|i| (i * 7 % 251) as u8));
        Bytes::from(b)
    }

    #[test]
    fn kinds_and_names() {
        assert_eq!(kind_of(&png(4)), Some("png"));
        assert_eq!(kind_of(b"\xff\xd8\xff\xe0"), Some("jpeg"));
        assert_eq!(kind_of(b"RIFF\0\0\0\0WEBPVP8 "), Some("webp"));
        assert_eq!(kind_of(b"\x1f\x8b\x08\0"), Some("geometry"));
        assert_eq!(kind_of(b"GIF89a"), None);
        assert!(!ok(b"") && !ok(b"<svg>"));
        let id = id_of(b"abc");
        assert_eq!(id, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert!(id_ok(&id) && !id_ok(&id.to_uppercase()) && !id_ok("../etc/passwd") && !id_ok(&id[..63]));
    }

    #[test]
    fn kept_by_name_checked_and_served_in_pieces() {
        let dir = std::env::temp_dir().join(format!("visit-assets-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        for disk in [Some(dir.clone()), None] {
            let mut a = Assets::open(disk.clone());
            let big = png(PIECE * 2 + 1000);
            let id = id_of(&big);
            assert!(!a.put(&id_of(b"other"), &big), "not under another's name");
            assert!(!a.put(&id_of(b"<svg>"), &Bytes::from_static(b"<svg>")), "not of a kind kept");
            assert!(!a.has(&id) && a.put(&id, &big) && a.has(&id));
            assert_eq!(a.get(&id), Some(big.clone()));
            let (t0, p0) = a.piece(&id, 0).unwrap();
            let (t1, p1) = a.piece(&id, PIECE).unwrap();
            let (t2, p2) = a.piece(&id, 2 * PIECE).unwrap();
            assert_eq!((t0, t1, t2, p0.len(), p1.len(), p2.len()), (big.len(), big.len(), big.len(), PIECE, PIECE, big.len() - 2 * PIECE));
            assert_eq!([p0, p1, p2].concat(), big.to_vec(), "its pieces, in order, are it");
            assert!(a.piece(&id, big.len()).is_none() && a.piece(&id_of(b"x"), 0).is_none());
            if disk.is_some() {
                assert!(Assets::open(disk).has(&id), "kept across runs");
            }
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_least_lately_used_go_past_the_cap() {
        let dir = std::env::temp_dir().join(format!("visit-assets-trim-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let mut a = Assets::open(Some(dir.clone()));
        let xs: Vec<Bytes> = (0..3).map(|i| png(100 + i)).collect();
        for x in &xs {
            a.put(&id_of(x), x);
            std::thread::sleep(std::time::Duration::from_millis(30));
        }
        a.get(&id_of(&xs[0]));   // used again: now the newest
        let left = trim(&dir, (xs[0].len() + xs[2].len()) as u64);
        assert_eq!(left, (xs[0].len() + xs[2].len()) as u64);
        assert_eq!(xs.iter().map(|x| a.has(&id_of(x))).collect::<Vec<_>>(), vec![true, false, true]);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
