"""Link-to-video saver with background downloads and live progress.
Run:  pip install -r requirements.txt  &&  python app.py   (needs ffmpeg)"""
import os, shutil, tempfile, threading, time, uuid
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, send_from_directory
import yt_dlp

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
ALLOWED = ("instagram.com", "youtube.com", "youtu.be", "twitter.com", "x.com")
JOBS = {}  # job_id -> dict(status, percent, speed, eta, error, path, dir, t)

def valid_url(u):
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower()
    except ValueError:
        return False
    return p.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in ALLOWED)

def format_for(q):
    if q == "audio":
        return "bestaudio/best"
    if q.isdigit():
        h = int(q)
        return f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]/best"
    return "bestvideo*+bestaudio/best"

def cleanup_old():
    for jid, j in list(JOBS.items()):
        if time.time() - j["t"] > 3600:
            shutil.rmtree(j.get("dir", ""), ignore_errors=True)
            JOBS.pop(jid, None)

def run_job(jid, url, q):
    job = JOBS[jid]
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job["percent"] = round(d["downloaded_bytes"] / total * 100, 1)
            job["speed"] = d.get("_speed_str", "").strip()
            job["eta"] = d.get("_eta_str", "").strip()
        elif d["status"] == "finished":
            job["status"] = "processing"
    opts = {"quiet": True, "noplaylist": True, "format": format_for(q),
            "outtmpl": os.path.join(job["dir"], "%(title).80s.%(ext)s"),
            "merge_output_format": "mp4", "progress_hooks": [hook],
            "concurrent_fragment_downloads": 4}
    if q == "audio":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}]
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
        job["path"] = os.path.join(job["dir"], os.listdir(job["dir"])[0])
        job["status"], job["percent"] = "done", 100
    except Exception as e:
        job["status"], job["error"] = "error", str(e)[:300]

@app.get("/")
def index():
    for folder in (os.path.join(BASE, "static"), BASE):
        if os.path.exists(os.path.join(folder, "index.html")):
            return send_from_directory(folder, "index.html")
    return "index.html not found", 404

@app.post("/api/info")
def info():
    url = (request.json or {}).get("url", "").strip()
    if not valid_url(url):
        return jsonify(error="Paste a link from Instagram, YouTube, or X."), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as y:
            d = y.extract_info(url, download=False)
    except Exception as e:
        return jsonify(error="Couldn't read that link: " + str(e)[:200]), 422
    heights = sorted({f["height"] for f in d.get("formats", []) if f.get("height")}, reverse=True)
    return jsonify(title=d.get("title") or "Untitled", thumbnail=d.get("thumbnail"),
                   uploader=d.get("uploader"), duration=d.get("duration"), heights=heights)

@app.post("/api/start")
def start():
    cleanup_old()
    body = request.json or {}
    url, q = body.get("url", ""), str(body.get("q", "best"))
    if not valid_url(url):
        return jsonify(error="Invalid link"), 400
    jid = uuid.uuid4().hex
    JOBS[jid] = {"status": "downloading", "percent": 0, "speed": "", "eta": "",
                 "error": None, "path": None, "dir": tempfile.mkdtemp(), "t": time.time()}
    threading.Thread(target=run_job, args=(jid, url, q), daemon=True).start()
    return jsonify(id=jid)

@app.get("/api/status/<jid>")
def status(jid):
    j = JOBS.get(jid)
    if not j:
        return jsonify(status="error", error="Job expired. Start again."), 404
    return jsonify({k: j[k] for k in ("status", "percent", "speed", "eta", "error")})

@app.get("/api/file/<jid>")
def file(jid):
    j = JOBS.get(jid)
    if not j or j["status"] != "done":
        return "Not ready", 404
    return send_file(j["path"], as_attachment=True)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
