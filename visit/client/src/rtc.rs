//! Voice over WebRTC (../SPEC.md 8.7, #71). Each page connects to every office it speaks to or hears from (its own,
//! the room it is in, a call's other side, a friend across a plot's side, the room voice it is in): one
//! RTCPeerConnection to each, made here with str0m, all on one UDP port. This office forwards: what a page sends it
//! goes, as the core decides (by where everyone is, as today), to the other pages connected to it. Nothing of voice
//! rides an iroh link any more; the links carry only the offers and answers (signaling).
//!
//! A page's connection carries its microphone (one sendonly audio m-line, sent while its office says someone hears it
//! there) and a pool of SLOTS recvonly audio m-lines: each slot carries one speaker at a time. Which speaker, from
//! where and how heard (in person, a call, a room voice), the page hears on the connection's data channel ("visit")
//! as the slot is given. Packets are forwarded as they come (RTP mode: nothing held back, nothing reordered here);
//! each slot's sequence numbers and timestamps are its own, carried on across a change of speaker, so the browser's
//! jitter buffer (NetEQ) sees one steady stream, with every loss on the way still a gap.
use std::{
    collections::HashMap,
    net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket as StdUdp},
    sync::{Arc, Once},
    time::{Duration, Instant},
};

use anyhow::{anyhow, Result};
use bytes::Bytes;
use serde_json::Value;
use str0m::{
    change::SdpOffer,
    channel::ChannelId,
    format::Codec,
    media::{Direction, MediaKind, Mid},
    net::{Protocol, Receive},
    rtp::{ExtensionValues, RtpWrite, SeqNo},
    Candidate, Event, IceConnectionState, Input, Output, Rtc,
};
use tokio::{net::UdpSocket, sync::mpsc};
use tracing::{debug, info};

use crate::core::Ev;

/// The most speakers one connection carries at once (its recvonly m-lines; the page offers this many).
pub const SLOTS: usize = 12;
/// A slot whose speaker has sent nothing for this long may carry another.
const SLOT_IDLE: Duration = Duration::from_millis(500);
/// A connection that is not up this long after its answer is given up.
const CONNECT_WITHIN: Duration = Duration::from_secs(30);
/// At most this many connections at once (a page and every link's page), and offers a second (all of them).
const MAX_PEERS: usize = 256;
/// The largest offer taken (an SDP of a microphone and SLOTS slots is about 6 kB).
pub const MAX_SDP: usize = 32 << 10;
/// The most packets of voice taken from one connection a second (one every 20 ms, and some to spare), and the most one
/// carries (20 ms of Opus is some tens of bytes): past either, dropped (nothing more goes on to anyone).
const PACKETS_PER_SECOND: u32 = 100;
const MAX_PAYLOAD: usize = 1000;
/// How often this office asks the STUN server again what its address looks like from outside.
const STUN_EVERY: Duration = Duration::from_secs(60);

/// Whose connection: this office's own page, or a page of the office at the other end of a link (by the visit's id).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum From {
    Page,
    Link(String),
}

/// A packet of someone's voice, as it came (its extended sequence number and timestamp, its loudness).
#[derive(Clone)]
pub struct Packet {
    pub seq: u64,
    pub ts: u32,
    pub payload: Arc<[u8]>,
    pub level: Option<i8>,
    pub vad: Option<bool>,
    pub at: Instant,
}

/// What came of input, or of time passing.
pub enum Got {
    Audio { peer: u64, pkt: Packet },
    /// A connection up (ICE and DTLS done).
    Up { peer: u64 },
    /// A connection gone (closed, failed, or never up).
    Gone { peer: u64 },
}

struct Slot {
    mid: Mid,
    /// The speaker it carries now (the core's name for them on this connection), and when they last sent.
    who: Option<String>,
    used: Instant,
    /// How the speaker's numbers map onto the slot's: (their first sequence number, its own then; their first
    /// timestamp, its own then), and the connection they came by (a new one numbers afresh).
    base: Option<(u64, u64, u32, u32)>,
    src: u64,
    /// The last the slot sent: its sequence number, timestamp, and when.
    last: Option<(u64, u32, Instant)>,
}

struct Peer {
    rtc: Rtc,
    from: From,
    /// The page's microphone (its sendonly m-line: recvonly here).
    mic: Option<Mid>,
    slots: Vec<Slot>,
    /// The data channel ("visit") once open; what to say on it until then.
    dc: Option<ChannelId>,
    said: Vec<String>,
    made: Instant,
    up: bool,
    /// The negotiated Opus payload type.
    pt: Option<str0m::format::PayloadParams>,
    /// Packets taken in the current second.
    taken: (Instant, u32),
}

pub struct Media {
    sock: Arc<UdpSocket>,
    /// The address this office's pages are told to reach it at: its main interface's, and the port bound.
    host: SocketAddr,
    /// Its address as seen from outside (STUN), once known.
    srflx: Option<SocketAddr>,
    stun: Option<SocketAddr>,
    stun_tx: Option<([u8; 12], Instant)>,
    peers: HashMap<u64, Peer>,
    next: u64,
    later: Vec<Got>,
}

static CRYPTO: Once = Once::new();

/// The address of this machine's main interface (the one a packet to the internet leaves by; nothing is sent).
fn main_ip() -> IpAddr {
    StdUdp::bind("0.0.0.0:0").and_then(|s| s.connect("192.0.2.1:9").map(|_| s)).and_then(|s| s.local_addr()).map(|a| a.ip())
        .ok().filter(|ip| !ip.is_unspecified()).unwrap_or(IpAddr::V4(Ipv4Addr::LOCALHOST))
}

impl Media {
    /// Binds the media port (`port`, or any) and starts reading it: each datagram to the core (Ev::Udp).
    pub async fn start(port: u16, core: mpsc::UnboundedSender<Ev>) -> Result<Media> {
        CRYPTO.call_once(|| str0m::crypto::from_feature_flags().install_process_default());
        let sock = Arc::new(UdpSocket::bind(SocketAddr::from((Ipv4Addr::UNSPECIFIED, port))).await?);
        let host = SocketAddr::new(main_ip(), sock.local_addr()?.port());
        info!("voice: WebRTC on UDP {host}");
        let reader = sock.clone();
        tokio::spawn(async move {
            let mut buf = vec![0u8; 2048];
            loop {
                match reader.recv_from(&mut buf).await {
                    Ok((n, src)) => {
                        if core.send(Ev::Udp { src, data: Bytes::copy_from_slice(&buf[..n]) }).is_err() {
                            return;
                        }
                    }
                    Err(e) => {
                        // (an ICMP "port unreachable" shows as an error on some systems: not the end of the socket)
                        debug!("voice: the media port: {e}");
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                }
            }
        });
        Ok(Media { sock, host, srflx: None, stun: None, stun_tx: None, peers: HashMap::new(), next: 1, later: Vec::new() })
    }

    /// The STUN server to ask (the lobby's), or none.
    pub fn set_stun(&mut self, stun: Option<SocketAddr>) {
        if self.stun != stun {
            self.stun = stun;
            self.stun_tx = None;
            self.ask_stun();
        }
    }

    fn ask_stun(&mut self) {
        let Some(stun) = self.stun else { return };
        let mut tx = [0u8; 12];
        getrandom_fill(&mut tx);
        let mut req = vec![0x00, 0x01, 0x00, 0x00, 0x21, 0x12, 0xA4, 0x42];
        req.extend_from_slice(&tx);
        let _ = self.sock.try_send_to(&req, stun);
        self.stun_tx = Some((tx, Instant::now()));
    }

    /// A STUN answer to this office's own question: its address from outside. True if it was one.
    fn stun_answer(&mut self, data: &[u8]) -> bool {
        let Some((tx, _)) = self.stun_tx else { return false };
        if data.len() < 20 || data[..2] != [0x01, 0x01] || data[4..8] != [0x21, 0x12, 0xA4, 0x42] || data[8..20] != tx {
            return false;
        }
        let mut at = 20;
        while at + 4 <= data.len() {
            let (t, len) = (u16::from_be_bytes([data[at], data[at + 1]]), u16::from_be_bytes([data[at + 2], data[at + 3]]) as usize);
            let v = &data[(at + 4).min(data.len())..(at + 4 + len).min(data.len())];
            if t == 0x0020 && v.len() >= 8 && v[1] == 0x01 {   // XOR-MAPPED-ADDRESS, IPv4
                let port = u16::from_be_bytes([v[2], v[3]]) ^ 0x2112;
                let ip = Ipv4Addr::new(v[4] ^ 0x21, v[5] ^ 0x12, v[6] ^ 0xA4, v[7] ^ 0x42);
                let addr = SocketAddr::from((ip, port));
                if self.srflx != Some(addr) {
                    info!("voice: from outside, this office is {addr}");
                    self.srflx = Some(addr);
                }
            }
            at += 4 + len.div_ceil(4) * 4;
        }
        true
    }

    /// An offer from a page (its own, or the far end of a link's): a connection for it, and the answer. A link's
    /// earlier connection goes (a page that reloads offers again).
    pub fn answer(&mut self, from: From, offer: &str) -> Result<(u64, String)> {
        if offer.len() > MAX_SDP {
            return Err(anyhow!("an offer is at most {} kB", MAX_SDP >> 10));
        }
        let old: Vec<u64> = self.peers.iter().filter(|(_, p)| p.from == from).map(|(id, _)| *id).collect();
        for id in old {
            self.close(id);
        }
        if self.peers.len() >= MAX_PEERS {
            return Err(anyhow!("too many voice connections"));
        }
        let offer = SdpOffer::from_sdp_string(offer).map_err(|e| anyhow!("not an offer: {e}"))?;
        let mut rtc = Rtc::builder().set_rtp_mode(true).clear_codecs().enable_opus(true, false).build(Instant::now());
        rtc.add_local_candidate(Candidate::host(self.host, "udp")?);
        if let Some(s) = self.srflx {
            if let Ok(c) = Candidate::server_reflexive(s, self.host, "udp") {
                rtc.add_local_candidate(c);
            }
        }
        let answer = rtc.sdp_api().accept_offer(offer).map_err(|e| anyhow!("the offer was refused: {e}"))?;
        let id = self.next;
        self.next += 1;
        let pt = rtc.codec_config().params().iter().find(|p| p.spec().codec == Codec::Opus).cloned();
        self.peers.insert(id, Peer { rtc, from, mic: None, slots: Vec::new(), dc: None, said: Vec::new(), made: Instant::now(), up: false, pt, taken: (Instant::now(), 0) });
        Ok((id, answer.to_sdp_string()))
    }

    pub fn close(&mut self, id: u64) {
        if let Some(mut p) = self.peers.remove(&id) {
            p.rtc.disconnect();
        }
    }

    pub fn from(&self, id: u64) -> Option<&From> {
        self.peers.get(&id).map(|p| &p.from)
    }

    /// A datagram on the media port.
    pub fn input(&mut self, src: SocketAddr, data: &[u8]) -> Vec<Got> {
        let mut got = Vec::new();
        if self.stun_answer(data) {
            return got;
        }
        let Ok(contents) = data.try_into() else { return got };
        let input = Input::Receive(Instant::now(), Receive { proto: Protocol::Udp, source: src, destination: self.host, contents });
        let Some(id) = self.peers.iter().find(|(_, p)| p.rtc.accepts(&input)).map(|(id, _)| *id) else { return got };
        let p = self.peers.get_mut(&id).expect("found");
        if let Err(e) = p.rtc.handle_input(input) {
            debug!("voice: connection {id}: {e}");
            p.rtc.disconnect();
        }
        self.poll(id, &mut got);
        got
    }

    /// Time passing: every connection's timers; those that are gone, or never came up.
    pub fn tick(&mut self) -> Vec<Got> {
        let now = Instant::now();
        if self.stun.is_some() && self.stun_tx.is_none_or(|(_, at)| now.duration_since(at) >= if self.srflx.is_some() { STUN_EVERY } else { Duration::from_secs(5) }) {
            self.ask_stun();
        }
        let mut got = Vec::new();
        let ids: Vec<u64> = self.peers.keys().copied().collect();
        for id in ids {
            let p = self.peers.get_mut(&id).expect("listed");
            if !p.up && p.made.elapsed() >= CONNECT_WITHIN {
                p.rtc.disconnect();
            }
            let _ = p.rtc.handle_input(Input::Timeout(now));
            self.poll(id, &mut got);
        }
        got
    }

    fn poll(&mut self, id: u64, got: &mut Vec<Got>) {
        got.append(&mut self.later);
        let Some(p) = self.peers.get_mut(&id) else { return };
        loop {
            if !p.rtc.is_alive() {
                break;
            }
            match p.rtc.poll_output() {
                Ok(Output::Timeout(_)) => return,
                Ok(Output::Transmit(t)) => {
                    let _ = self.sock.try_send_to(&t.contents, t.destination);   // (a full socket: late is lost)
                }
                Ok(Output::Event(e)) => match e {
                    Event::Connected => {
                        p.up = true;
                        got.push(Got::Up { peer: id });
                    }
                    Event::IceConnectionStateChange(IceConnectionState::Disconnected) => p.rtc.disconnect(),
                    Event::MediaAdded(m) if m.kind == MediaKind::Audio => match m.direction {
                        Direction::RecvOnly | Direction::SendRecv if p.mic.is_none() => p.mic = Some(m.mid),
                        Direction::SendOnly if p.slots.len() < SLOTS => {
                            p.slots.push(Slot { mid: m.mid, who: None, used: Instant::now(), base: None, src: 0, last: None })
                        }
                        _ => {}
                    },
                    Event::ChannelOpen(cid, _) => {
                        p.dc = Some(cid);
                        for s in std::mem::take(&mut p.said) {
                            if let Some(mut ch) = p.rtc.channel(cid) {
                                let _ = ch.write(false, s.as_bytes());
                            }
                        }
                    }
                    Event::ChannelClose(_) => p.dc = None,
                    Event::RtpPacket(r) => {   // (a page sends only its microphone)
                        if p.taken.0.elapsed() >= Duration::from_secs(1) {
                            p.taken = (Instant::now(), 0);
                        }
                        p.taken.1 += 1;
                        if p.taken.1 > PACKETS_PER_SECOND || r.payload.len() > MAX_PAYLOAD {
                            continue;
                        }
                        let (level, vad) = (r.header.ext_vals.audio_level, r.header.ext_vals.voice_activity);
                        got.push(Got::Audio { peer: id, pkt: Packet { seq: *r.seq_no, ts: r.header.timestamp, payload: r.payload.clone(), level, vad, at: r.timestamp } });
                    }
                    _ => {}
                },
                Err(e) => {
                    debug!("voice: connection {id}: {e}");
                    p.rtc.disconnect();
                }
            }
        }
        self.peers.remove(&id);
        got.push(Got::Gone { peer: id });
    }

    /// A packet of a speaker's (`who`: the core's name for them, the same for as long as they speak on this
    /// connection), come by connection `from`, to a connection: on the slot that carries them, else a free one (the
    /// page told who is on it first, with `label`). None free: not heard (the most at once is SLOTS).
    pub fn forward(&mut self, to: u64, from: u64, who: &str, label: &Value, pkt: &Packet) {
        let Some(p) = self.peers.get_mut(&to) else { return };
        if !p.up {
            return;
        }
        let Some(pt) = p.pt.as_ref().map(|pp| pp.pt()) else { return };
        let now = Instant::now();
        let i = match p.slots.iter().position(|s| s.who.as_deref() == Some(who)) {
            Some(i) => i,
            None => {
                let Some(i) = p.slots.iter().enumerate().filter(|(_, s)| s.who.is_none() || now.duration_since(s.used) >= SLOT_IDLE)
                    .min_by_key(|(_, s)| (s.who.is_some(), s.used)).map(|(i, _)| i) else {
                    return;
                };
                let s = &mut p.slots[i];
                s.who = Some(who.to_string());
                s.base = None;
                let mut l = label.clone();
                l["mid"] = Value::String(s.mid.to_string());
                let text = l.to_string();
                match p.dc.and_then(|c| p.rtc.channel(c)) {
                    Some(mut ch) => {
                        let _ = ch.write(false, text.as_bytes());
                    }
                    None => p.said.push(text),
                }
                i
            }
        };
        let s = &mut p.slots[i];
        s.used = now;
        if s.src != from {   // (their page connected again: its numbers start afresh)
            s.src = from;
            s.base = None;
        }
        // This speaker's numbers onto the slot's: carried on from where it was (or from a start of its own), by the
        // time since it last sent; packets older than the first one taken are not sent.
        let (in_seq0, out_seq0, in_ts0, out_ts0) = *s.base.get_or_insert_with(|| {
            let (seq, ts) = match s.last {
                Some((seq, ts, at)) => (seq + 1, ts.wrapping_add(((now.duration_since(at).as_secs_f64() * 48_000.0) as u32).max(960))),
                None => (1, 0),
            };
            (pkt.seq, seq, pkt.ts, ts)
        });
        if pkt.seq < in_seq0 {
            return;
        }
        let seq = out_seq0 + (pkt.seq - in_seq0);
        let ts = out_ts0.wrapping_add(pkt.ts.wrapping_sub(in_ts0));
        if s.last.is_none_or(|(l, _, _)| seq > l) {
            s.last = Some((seq, ts, now));
        }
        let ext = ExtensionValues { audio_level: pkt.level, voice_activity: pkt.vad, ..Default::default() };
        let mid = s.mid;
        if let Some(tx) = p.rtc.direct_api().stream_tx_by_mid(mid, None) {
            tx.write_rtp(RtpWrite::new(pt, SeqNo::from(seq), ts, pkt.at, pkt.payload.clone()).ext_vals(ext));
        }
        let mut later = std::mem::take(&mut self.later);
        self.poll(to, &mut later);   // (sent now; what else comes of it, with the next input or tick)
        self.later = later;
    }

}

fn getrandom_fill(b: &mut [u8]) {
    // A STUN transaction id need only be unpredictable enough not to be mistaken: the time, and an address.
    let seed = Instant::now().elapsed().as_nanos() as u64 ^ (b.as_ptr() as u64).rotate_left(17)
        ^ std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos() as u64);
    let mut x = seed | 1;
    for v in b.iter_mut() {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        *v = x as u8;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_stun_answer_says_where_this_office_is() {
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async {
            let (tx, _rx) = mpsc::unbounded_channel();
            let mut m = Media::start(0, tx).await.unwrap();
            m.stun = Some("127.0.0.1:9".parse().unwrap());
            m.ask_stun();
            let (id, _) = m.stun_tx.unwrap();
            // 203.0.113.7:4242, as XOR-MAPPED-ADDRESS
            let port = (4242u16 ^ 0x2112).to_be_bytes();
            let ip = [203 ^ 0x21, 0 ^ 0x12, 113 ^ 0xA4, 7 ^ 0x42];
            let mut a = vec![0x01, 0x01, 0x00, 0x0C, 0x21, 0x12, 0xA4, 0x42];
            a.extend_from_slice(&id);
            a.extend_from_slice(&[0x00, 0x20, 0x00, 0x08, 0x00, 0x01, port[0], port[1]]);
            a.extend_from_slice(&ip);
            let mut wrong = a.clone();
            wrong[19] ^= 1;
            assert!(!m.stun_answer(&wrong), "another question's answer is not this office's");
            assert!(m.stun_answer(&a));
            assert_eq!(m.srflx, Some("203.0.113.7:4242".parse().unwrap()));
        });
    }
}
