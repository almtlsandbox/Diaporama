#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diaporama plein écran (photos + vidéos) servi dans le navigateur.

Utilisation :
    py slideshow.py "C:\\Users\\Moi\\Pictures\\Vacances"
    py slideshow.py "D:\\Photos" --duree 8 --aleatoire
    py slideshow.py "D:\\Photos" --reseau        (accessible depuis une télé / tablette)

Aucune dépendance : Python 3.8+ suffit.
"""
import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

IMAGES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".avif": "image/avif", ".svg": "image/svg+xml",
}
VIDEOS = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".ogv": "video/ogg",
}
TYPES = dict(IMAGES, **VIDEOS)

ROOT = ""       # dossier servi (chemin absolu réel)
CLI = {}        # réglages imposés par la ligne de commande


def scan():
    """Liste récursive des médias : chemin relatif (avec /), type, date de modification."""
    items = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "$")))
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in TYPES or name.startswith("."):
                continue
            full = os.path.join(base, name)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            items.append({"p": rel, "t": "v" if ext in VIDEOS else "i", "m": mtime})
    return items


def resolve(rel):
    """Chemin relatif d'URL -> chemin absolu, seulement s'il reste dans ROOT."""
    parts = [p for p in unquote(rel).split("/") if p]
    if not parts or any(p in (".", "..") or "\\" in p or ":" in p for p in parts):
        return None
    full = os.path.realpath(os.path.join(ROOT, *parts))
    try:
        inside = os.path.commonpath([os.path.normcase(full), os.path.normcase(ROOT)]) == os.path.normcase(ROOT)
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(full):
        return None
    if os.path.splitext(full)[1].lower() not in TYPES:
        return None
    return full


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_bytes(self, body, ctype, status=200, head=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_HEAD(self):
        self.route(head=True)

    def do_GET(self):
        self.route(head=False)

    def route(self, head):
        try:
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                cfg = json.dumps(CLI, ensure_ascii=False).replace("</", "<\\/")
                html = PAGE.replace("__CLI__", cfg).encode("utf-8")
                self.send_bytes(html, "text/html; charset=utf-8", head=head)
            elif path == "/api/list":
                body = json.dumps(scan(), ensure_ascii=False).encode("utf-8")
                self.send_bytes(body, "application/json; charset=utf-8", head=head)
            elif path.startswith("/media/"):
                self.send_media(path[len("/media/"):], head)
            else:
                self.send_bytes(b"Introuvable", "text/plain; charset=utf-8", 404, head)
        except (ConnectionError, TimeoutError):
            pass  # le navigateur a coupé (changement de diapo, fermeture d'onglet)

    def send_media(self, rel, head):
        full = resolve(rel)
        if not full:
            return self.send_bytes(b"Introuvable", "text/plain; charset=utf-8", 404, head)
        size = os.path.getsize(full)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes=") and size > 0:
            try:
                a, b = rng[6:].split(",")[0].strip().split("-", 1)
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    if b:
                        end = min(int(b), size - 1)
                if start > end or start >= size:
                    raise ValueError
                status = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1 if size > 0 else 0
        self.send_response(status)
        self.send_header("Content-Type", TYPES[os.path.splitext(full)[1].lower()])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if head:
            return
        with open(full, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # aucun paquet n'est envoyé
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    global ROOT, CLI
    ap = argparse.ArgumentParser(description="Diaporama plein écran de photos et vidéos dans le navigateur.")
    ap.add_argument("dossier", nargs="?", help="dossier contenant les photos et vidéos")
    ap.add_argument("--duree", type=float, help="secondes par photo (défaut : 5)")
    ap.add_argument("--ordre", choices=["nom", "date", "aleatoire"], help="ordre de défilement")
    ap.add_argument("--aleatoire", action="store_true", help="raccourci pour --ordre aleatoire")
    ap.add_argument("--auto", action="store_true", help="démarre sans attendre de clic (vidéos muettes, pas de plein écran automatique)")
    ap.add_argument("--reseau", action="store_true", help="rend le diaporama accessible aux autres appareils du réseau local")
    ap.add_argument("--port", type=int, default=8000, help="port (défaut : 8000)")
    ap.add_argument("--sans-navigateur", action="store_true", help="n'ouvre pas le navigateur automatiquement")
    args = ap.parse_args()

    folder = args.dossier
    if not folder:
        try:
            folder = input("Chemin du dossier de photos/vidéos : ")
        except EOFError:
            folder = ""
    folder = os.path.expanduser(folder.strip().strip('"').strip("'"))
    if not folder or not os.path.isdir(folder):
        print("Dossier introuvable : %s" % (folder or "(vide)"))
        sys.exit(1)
    ROOT = os.path.realpath(folder)

    CLI = {"dossier": os.path.basename(ROOT.rstrip("\\/")) or ROOT}
    if args.duree:
        CLI["duree"] = max(0.5, args.duree)
    if args.aleatoire:
        CLI["ordre"] = "aleatoire"
    elif args.ordre:
        CLI["ordre"] = args.ordre
    if args.auto:
        CLI["auto"] = True

    host = "0.0.0.0" if args.reseau else "127.0.0.1"
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer((host, port), Handler)
            break
        except OSError:
            continue
    if not server:
        print("Aucun port libre entre %d et %d." % (args.port, args.port + 19))
        sys.exit(1)
    server.daemon_threads = True

    items = scan()
    n_img = sum(1 for i in items if i["t"] == "i")
    url = "http://localhost:%d" % port
    print("Dossier  : %s" % ROOT)
    print("Contenu  : %d photo(s), %d vidéo(s)" % (n_img, len(items) - n_img))
    print("Adresse  : %s" % url)
    if args.reseau:
        ip = lan_ip()
        if ip:
            print("Réseau   : http://%s:%d  (autres appareils du même réseau)" % (ip, port))
    print("Ctrl+C pour arrêter.")

    if not args.sans_navigateur:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
        server.server_close()


PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diaporama</title>
<style>
  :root { --fit: contain; --accent: #e8b04a; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: #000; color: #f2f2f2; overflow: hidden;
    font: 15px/1.4 "Segoe UI", system-ui, sans-serif; }
  body.idle { cursor: none; }
  #stage { position: fixed; inset: 0; overflow: hidden; background: #000; }
  .slide { position: absolute; inset: 0; will-change: opacity, transform; }
  .slide img, .slide video { width: 100%; height: 100%; object-fit: var(--fit); display: block; }

  #caption { position: fixed; left: 28px; bottom: 24px; max-width: 70vw; padding: 6px 12px;
    background: rgba(0,0,0,.45); border-radius: 6px; font-size: 17px; text-shadow: 0 1px 2px #000;
    transition: bottom .25s; pointer-events: none; }
  #caption:empty { display: none; }
  body:not(.idle) #caption { bottom: 84px; }

  #bar { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); display: flex; align-items: center;
    gap: 4px; padding: 6px 10px; background: rgba(20,20,20,.82); border: 1px solid rgba(255,255,255,.12);
    border-radius: 12px; backdrop-filter: blur(8px); transition: opacity .25s; }
  body.idle #bar { opacity: 0; pointer-events: none; }
  #bar button { width: 42px; height: 42px; border: 0; border-radius: 8px; background: none; color: inherit;
    font-size: 19px; cursor: pointer; }
  #bar button:hover { background: rgba(255,255,255,.14); }
  #counter { min-width: 86px; text-align: center; font-variant-numeric: tabular-nums; opacity: .8; }

  #panel { position: fixed; top: 0; right: 0; bottom: 0; width: 330px; max-width: 100vw; padding: 22px 22px 30px;
    background: rgba(18,18,18,.94); border-left: 1px solid rgba(255,255,255,.12); overflow-y: auto;
    transform: translateX(100%); transition: transform .25s; }
  #panel.open { transform: none; }
  #panel h2 { margin: 0 0 16px; font-size: 18px; font-weight: 600; }
  #panel label { display: block; margin: 14px 0 5px; opacity: .75; font-size: 13px; }
  #panel label.check { display: flex; align-items: center; gap: 9px; opacity: 1; font-size: 15px; cursor: pointer; }
  #panel select, #panel input[type=number] { width: 100%; padding: 8px 10px; border-radius: 7px; font: inherit;
    border: 1px solid rgba(255,255,255,.2); background: #262626; color: inherit; }
  #panel input[type=range] { width: 100%; accent-color: var(--accent); }
  #panel input[type=checkbox] { width: 17px; height: 17px; accent-color: var(--accent); }
  #panel .val { float: right; opacity: 1; color: var(--accent); }
  #panel .keys { margin-top: 24px; padding-top: 14px; border-top: 1px solid rgba(255,255,255,.12);
    font-size: 13px; opacity: .65; line-height: 1.8; }
  kbd { padding: 1px 6px; border: 1px solid rgba(255,255,255,.3); border-radius: 4px; font: inherit; }
  #close { position: absolute; top: 14px; right: 14px; width: 34px; height: 34px; border: 0; border-radius: 7px;
    background: none; color: inherit; font-size: 18px; cursor: pointer; }
  #close:hover { background: rgba(255,255,255,.14); }

  #start, #msg { position: fixed; inset: 0; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 14px; text-align: center; padding: 24px; }
  #start { background: #0b0b0b; cursor: pointer; }
  #start h1 { margin: 0; font-size: clamp(26px, 5vw, 52px); font-weight: 600; }
  #start p { margin: 0; opacity: .65; }
  #start button { margin-top: 18px; padding: 14px 38px; border: 0; border-radius: 10px; background: var(--accent);
    color: #1a1200; font: inherit; font-size: 19px; font-weight: 600; cursor: pointer; }
  #msg { pointer-events: none; font-size: 20px; opacity: .8; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<div id="stage"></div>
<div id="msg" hidden></div>
<div id="caption"></div>

<div id="bar">
  <button id="bPrev" title="Précédent (←)">&#9664;</button>
  <button id="bPlay" title="Pause / lecture (Espace)">&#10074;&#10074;</button>
  <button id="bNext" title="Suivant (→)">&#9654;</button>
  <span id="counter">–</span>
  <button id="bFull" title="Plein écran (F)">&#9974;</button>
  <button id="bSet" title="Réglages (S)">&#9881;</button>
</div>

<aside id="panel">
  <button id="close" title="Fermer">&#10005;</button>
  <h2>Réglages</h2>

  <label>Durée par photo <span class="val" id="vDuree"></span></label>
  <input type="range" id="duree" min="1" max="60" step="1">

  <label>Ordre</label>
  <select id="ordre">
    <option value="nom">Par nom</option>
    <option value="date">Par date (plus ancien d'abord)</option>
    <option value="aleatoire">Aléatoire</option>
  </select>

  <label>Transition</label>
  <select id="transition">
    <option value="fondu">Fondu</option>
    <option value="glissement">Glissement</option>
    <option value="aucune">Aucune</option>
  </select>

  <label>Durée de la transition <span class="val" id="vTrans"></span></label>
  <input type="range" id="dureeTransition" min="0.2" max="3" step="0.1">

  <label>Cadrage</label>
  <select id="cadrage">
    <option value="contain">Image entière (bandes noires)</option>
    <option value="cover">Remplir l'écran (recadré)</option>
  </select>

  <label>Légende</label>
  <select id="legende">
    <option value="aucune">Aucune</option>
    <option value="nom">Nom du fichier</option>
    <option value="date">Date</option>
    <option value="les-deux">Nom et date</option>
  </select>

  <label>Durée maximale des vidéos, en secondes (0 = en entier)</label>
  <input type="number" id="maxVideo" min="0" step="1">

  <label class="check" style="margin-top:18px"><input type="checkbox" id="son"> Son des vidéos</label>
  <label class="check"><input type="checkbox" id="sousDossiers"> Inclure les sous-dossiers</label>
  <label class="check"><input type="checkbox" id="boucle"> Recommencer à la fin</label>

  <div class="keys">
    <kbd>←</kbd> <kbd>→</kbd> précédent / suivant<br>
    <kbd>Espace</kbd> pause &nbsp; <kbd>F</kbd> plein écran<br>
    <kbd>S</kbd> réglages &nbsp; <kbd>Échap</kbd> quitter le plein écran
  </div>
</aside>

<div id="start">
  <h1 id="sTitle">Diaporama</h1>
  <p id="sCount">Chargement…</p>
  <button id="sGo">Démarrer</button>
  <p>Cliquez n'importe où ou appuyez sur Entrée</p>
</div>

<script>
const CLI = __CLI__;
const DEF = { duree: 5, ordre: 'nom', transition: 'fondu', dureeTransition: 1, cadrage: 'contain',
              legende: 'aucune', maxVideo: 0, son: false, sousDossiers: true, boucle: true };
const KEY = 'diaporama-reglages';
const $ = id => document.getElementById(id);

function loadSaved() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
function save() {
  const out = {}; for (const k in DEF) out[k] = cfg[k];
  try { localStorage.setItem(KEY, JSON.stringify(out)); } catch (e) {}
}
const cfg = Object.assign({}, DEF, loadSaved());
for (const k in DEF) if (k in CLI) cfg[k] = CLI[k];   // la ligne de commande a priorité

const stage = $('stage');
let all = [], list = [], idx = 0;
let token = 0, timer = null, paused = false, started = false;
let current = null, pending = null, lastDir = 1, errors = 0;

/* ---------- liste ---------- */
async function refresh() {
  try { all = await (await fetch('/api/list', { cache: 'no-store' })).json(); } catch (e) {}
}
function buildList(keepPath) {
  list = all.filter(it => cfg.sousDossiers || !it.p.includes('/'));
  if (cfg.ordre === 'date') list.sort((a, b) => a.m - b.m);
  else if (cfg.ordre === 'aleatoire') {
    for (let i = list.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1)); [list[i], list[j]] = [list[j], list[i]];
    }
  } else list.sort((a, b) => a.p.localeCompare(b.p, 'fr', { numeric: true, sensitivity: 'base' }));
  if (keepPath) {
    const i = list.findIndex(it => it.p === keepPath);
    if (i > 0 && cfg.ordre === 'aleatoire') { [list[0], list[i]] = [list[i], list[0]]; idx = 0; }
    else idx = Math.max(0, i);
  }
}
const urlOf = it => '/media/' + it.p.split('/').map(encodeURIComponent).join('/');

/* ---------- défilement ---------- */
function message(text) { $('msg').textContent = text || ''; $('msg').hidden = !text; }

function dropVideo(v) { if (v) { try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {} } }

async function advance(dir) {
  lastDir = dir;
  clearTimeout(timer);
  let n = idx + dir;
  if (n >= list.length) {
    if (!cfg.boucle && list.length) { setPaused(true); return; }
    await refresh(); buildList(null); n = 0;       // reprend les fichiers ajoutés entre-temps
  } else if (n < 0) n = list.length - 1;
  idx = n;
  show();
}

function show() {
  clearTimeout(timer);
  const my = ++token;
  if (pending) { dropVideo(pending); pending = null; }
  const it = list[idx];
  if (!it) {
    message('Aucune photo ou vidéo trouvée dans ce dossier.');
    if (current) { current.remove(); current = null; }
    updateInfo();
    timer = setTimeout(async () => { await refresh(); buildList(null); idx = 0; show(); }, 5000);
    return;
  }
  const slide = document.createElement('div');
  slide.className = 'slide';
  const fail = () => {
    if (my !== token) return;
    if (++errors >= list.length) { message('Aucun fichier lisible par ce navigateur.'); return; }
    advance(lastDir);
  };
  if (it.t === 'v') {
    const v = document.createElement('video');
    pending = v;
    v.muted = !cfg.son; v.playsInline = true; v.preload = 'auto';
    v.addEventListener('loadeddata', () => {
      if (my !== token) return;
      pending = null;
      enter(slide, it);
      if (!paused) playVideo(v);
      if (cfg.maxVideo > 0 && !paused) timer = setTimeout(() => advance(1), cfg.maxVideo * 1000);
    }, { once: true });
    v.addEventListener('ended', () => { if (my === token) advance(1); });
    v.addEventListener('error', fail);
    v.src = urlOf(it);
    slide.appendChild(v);
  } else {
    const img = new Image();
    img.onload = () => { if (my !== token) return; enter(slide, it); arm(); };
    img.onerror = fail;
    img.src = urlOf(it);
    slide.appendChild(img);
  }
}

function playVideo(v) {
  const p = v.play();
  if (p) p.catch(() => { v.muted = true; v.play().catch(() => {}); });   // son bloqué -> muet
}

function arm() {
  clearTimeout(timer);
  if (!paused) timer = setTimeout(() => advance(1), cfg.duree * 1000);
}

function enter(slide, it) {
  errors = 0; message('');
  const old = current; current = slide; slide.item = it;
  const kind = old ? cfg.transition : 'aucune';
  const T = kind === 'aucune' ? 0 : cfg.dureeTransition * 1000;
  const tr = 'opacity ' + T + 'ms ease, transform ' + T + 'ms ease';
  if (kind === 'fondu') slide.style.opacity = 0;
  if (kind === 'glissement') slide.style.transform = 'translateX(' + (lastDir * 100) + '%)';
  stage.appendChild(slide);
  void slide.offsetWidth;
  slide.style.transition = tr; slide.style.opacity = 1; slide.style.transform = 'none';
  if (old) {
    const ov = old.querySelector('video'); if (ov) ov.muted = true;
    old.style.transition = tr;
    if (kind === 'fondu') old.style.opacity = 0;
    if (kind === 'glissement') old.style.transform = 'translateX(' + (-lastDir * 100) + '%)';
    setTimeout(() => { dropVideo(ov); old.remove(); }, T + 60);
  }
  updateInfo();
  const next = list[(idx + 1) % list.length];           // précharge la photo suivante
  if (next && next.t === 'i') { new Image().src = urlOf(next); }
}

function updateInfo() {
  $('counter').textContent = list.length ? (idx + 1) + ' / ' + list.length : '0';
  const it = current && current.item;
  let text = '';
  if (it && cfg.legende !== 'aucune') {
    const name = it.p.split('/').pop().replace(/\.[^.]+$/, '');
    const date = new Date(it.m * 1000).toLocaleDateString('fr-CA', { year: 'numeric', month: 'long', day: 'numeric' });
    text = cfg.legende === 'nom' ? name : cfg.legende === 'date' ? date : name + ' · ' + date;
  }
  $('caption').textContent = text;
}

function setPaused(p) {
  paused = p;
  $('bPlay').innerHTML = p ? '&#9654;' : '&#10074;&#10074;';
  const v = current && current.querySelector('video');
  if (p) { clearTimeout(timer); if (v) v.pause(); }
  else if (v) { if (v.ended) advance(1); else playVideo(v); }
  else if (current) {
    if (!cfg.boucle && idx >= list.length - 1) { idx = -1; advance(1); } else arm();
  }
}

/* ---------- plein écran ---------- */
function toggleFull() {
  const d = document, el = d.documentElement;
  if (d.fullscreenElement || d.webkitFullscreenElement) (d.exitFullscreen || d.webkitExitFullscreen).call(d);
  else { const r = el.requestFullscreen || el.webkitRequestFullscreen; if (r) { const p = r.call(el); if (p && p.catch) p.catch(() => {}); } }
}

/* ---------- réglages ---------- */
const FIELDS = ['duree', 'ordre', 'transition', 'dureeTransition', 'cadrage', 'legende', 'maxVideo', 'son', 'sousDossiers', 'boucle'];
function paintPanel() {
  for (const k of FIELDS) { const el = $(k); if (el.type === 'checkbox') el.checked = !!cfg[k]; else el.value = cfg[k]; }
  $('vDuree').textContent = cfg.duree + ' s';
  $('vTrans').textContent = Number(cfg.dureeTransition).toFixed(1) + ' s';
  document.documentElement.style.setProperty('--fit', cfg.cadrage);
}
function onChange(k) {
  const el = $(k);
  cfg[k] = el.type === 'checkbox' ? el.checked : (el.type === 'range' || el.type === 'number') ? Math.max(0, Number(el.value) || 0) : el.value;
  save(); paintPanel();
  if (!started) { if (k === 'sousDossiers') { buildList(null); paintStart(); } return; }
  const v = current && current.querySelector('video');
  if (k === 'ordre' || k === 'sousDossiers') {
    const keep = current && current.item ? current.item.p : null;
    buildList(keep);
    if (keep && list[idx] && list[idx].p === keep) updateInfo(); else { idx = 0; show(); }
  } else if (k === 'duree') { if (!v && current) arm(); }
  else if (k === 'son') { if (v) v.muted = !cfg.son; }
  else if (k === 'maxVideo') {
    if (v && !paused) { clearTimeout(timer); if (cfg.maxVideo > 0) timer = setTimeout(() => advance(1), cfg.maxVideo * 1000); }
  } else if (k === 'legende') updateInfo();
}
for (const k of FIELDS) $(k).addEventListener('input', () => onChange(k));
const panel = $('panel');
function togglePanel(force) { panel.classList.toggle('open', force); wake(); }

/* ---------- commandes ---------- */
let idleTimer = null;
function wake() {
  document.body.classList.remove('idle');
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { if (!panel.classList.contains('open')) document.body.classList.add('idle'); }, 3000);
}
['mousemove', 'mousedown', 'touchstart'].forEach(e => addEventListener(e, wake, { passive: true }));

$('bPrev').onclick = () => advance(-1);
$('bNext').onclick = () => advance(1);
$('bPlay').onclick = () => setPaused(!paused);
$('bFull').onclick = toggleFull;
$('bSet').onclick = () => togglePanel();
$('close').onclick = () => togglePanel(false);
stage.addEventListener('dblclick', toggleFull);
stage.addEventListener('click', () => { if (panel.classList.contains('open')) togglePanel(false); });

addEventListener('keydown', e => {
  if (e.target.matches('input[type=number], select')) return;
  if (!started) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); begin(true); } return; }
  const k = e.key;
  if (k === 'ArrowRight' || k === 'PageDown') advance(1);
  else if (k === 'ArrowLeft' || k === 'PageUp') advance(-1);
  else if (k === ' ') { e.preventDefault(); setPaused(!paused); }
  else if (k === 'f' || k === 'F') toggleFull();
  else if (k === 's' || k === 'S') togglePanel();
  else if (k === 'Escape') togglePanel(false);
  else return;
  wake();
});

/* ---------- démarrage ---------- */
function paintStart() {
  const ni = list.filter(i => i.t === 'i').length, nv = list.length - ni;
  $('sCount').textContent = list.length ? ni + ' photo(s), ' + nv + ' vidéo(s)' : 'Aucune photo ou vidéo trouvée';
}
function begin(full) {
  if (started) return;
  started = true;
  $('start').hidden = true;
  if (full) toggleFull();
  idx = 0; show(); wake();
}
$('start').addEventListener('click', () => begin(true));

(async function init() {
  if (CLI.dossier) { $('sTitle').textContent = CLI.dossier; document.title = CLI.dossier + ' – Diaporama'; }
  paintPanel();
  await refresh(); buildList(null); paintStart();
  if (CLI.auto || new URLSearchParams(location.search).has('auto')) begin(false);
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
