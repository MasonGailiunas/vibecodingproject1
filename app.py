from flask import Flask, request, jsonify
import requests
import os
import math
import json
app = Flask(__name__)

SPOTIFY_CLIENT_ID = "bcdd89549ea649d2a49f49df49a8310c"
SPOTIFY_CLIENT_SECRET = "2ae76df7cffd4312887fa6ac34feb32e"

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ranker_state.json")


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


def get_spotify_token():
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
    )
    return response.json().get("access_token")


def extract_playlist_id(url_or_id):
    if "spotify.com/playlist/" in url_or_id:
        part = url_or_id.split("spotify.com/playlist/")[1]
        return part.split("?")[0].split("/")[0]
    return url_or_id.strip()


def get_playlist_tracks(playlist_id, token):
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    headers = {"Authorization": f"Bearer {token}"}

    while url:
        resp = requests.get(url, headers=headers, params={"limit": 100})
        data = resp.json()
        if "error" in data:
            return None, data["error"]["message"]

        for item in data.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                tracks.append({
                    "id": track["id"],
                    "name": track["name"],
                    "artist": ", ".join(a["name"] for a in track["artists"]),
                    "album": track["album"]["name"],
                    "image": track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                    "preview_url": track.get("preview_url"),
                })
        url = data.get("next")

    return tracks, None


class LiveMergeSort:
    def __init__(self, n):
        self.n = n
        self.sublists = [[i] for i in range(n)]
        self.pending_merge = None
        self.finished = False
        self.final_order = None
        self.comp_count = 0

    def to_dict(self):
        return {
            "n": self.n,
            "sublists": self.sublists,
            "pending_merge": self.pending_merge,
            "finished": self.finished,
            "final_order": self.final_order,
            "comp_count": self.comp_count,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(d["n"])
        obj.sublists = d["sublists"]
        obj.pending_merge = d["pending_merge"]
        obj.finished = d["finished"]
        obj.final_order = d["final_order"]
        obj.comp_count = d["comp_count"]
        return obj

    def _start_next_merge(self):
        if len(self.sublists) <= 1:
            self.finished = True
            self.final_order = self.sublists[0] if self.sublists else []
            return False
        left = self.sublists.pop(0)
        right = self.sublists.pop(0)
        self.pending_merge = {"left": left, "right": right, "merged": [], "li": 0, "ri": 0}
        return True

    def get_next_comparison(self):
        if self.finished:
            return None
        if self.pending_merge is None:
            if not self._start_next_merge():
                return None
        m = self.pending_merge
        while True:
            if m["li"] < len(m["left"]) and m["ri"] < len(m["right"]):
                return (m["left"][m["li"]], m["right"][m["ri"]])
            else:
                m["merged"].extend(m["left"][m["li"]:])
                m["merged"].extend(m["right"][m["ri"]:])
                self.sublists.append(m["merged"])
                self.pending_merge = None
                if not self._start_next_merge():
                    return None
                m = self.pending_merge

    def record_choice(self, winner_original_idx):
        if not self.pending_merge:
            return
        m = self.pending_merge
        left_item = m["left"][m["li"]]
        right_item = m["right"][m["ri"]]
        if winner_original_idx == left_item:
            m["merged"].append(left_item)
            m["li"] += 1
        else:
            m["merged"].append(right_item)
            m["ri"] += 1
        self.comp_count += 1

    def progress(self):
        total = self.n * math.log2(self.n) if self.n > 1 else 1
        return min(99, int((self.comp_count / max(total, 1)) * 100))


@app.route("/")
def index():
    dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(dir, "templates", "index.html"), "r") as f:
        return f.read()


@app.route("/load_playlist", methods=["POST"])
def load_playlist():
    data = request.json
    playlist_input = data.get("playlist", "").strip()

    if not playlist_input:
        return jsonify({"error": "Please enter a playlist URL or ID"}), 400

    playlist_id = extract_playlist_id(playlist_input)

    try:
        token = get_spotify_token()
        tracks, error = get_playlist_tracks(playlist_id, token)
    except Exception as e:
        return jsonify({"error": f"Failed to connect to Spotify: {str(e)}"}), 500

    if error:
        return jsonify({"error": error}), 400
    if not tracks:
        return jsonify({"error": "No tracks found in playlist"}), 400
    if len(tracks) < 2:
        return jsonify({"error": "Playlist needs at least 2 songs"}), 400
    if len(tracks) > 100:
        tracks = tracks[:100]

    sorter = LiveMergeSort(len(tracks))
    pair = sorter.get_next_comparison()

    if pair is None:
        return jsonify({"error": "Sorting error"}), 500

    save_state({"tracks": tracks, "sorter": sorter.to_dict()})

    a, b = pair
    return jsonify({
        "success": True,
        "total": len(tracks),
        "track_a": {**tracks[a], "index": a},
        "track_b": {**tracks[b], "index": b},
        "progress": 0,
        "comp_count": 0,
        "estimated_total": int(len(tracks) * math.log2(len(tracks))) if len(tracks) > 1 else 1,
    })


@app.route("/choose", methods=["POST"])
def choose():
    data = request.json
    winner_idx = data.get("winner")

    if winner_idx is None:
        return jsonify({"error": "No choice provided"}), 400

    state = load_state()
    if not state:
        return jsonify({"error": "Session expired. Please reload the playlist."}), 400

    tracks = state["tracks"]
    sorter = LiveMergeSort.from_dict(state["sorter"])

    sorter.record_choice(winner_idx)
    pair = sorter.get_next_comparison()

    if sorter.finished and sorter.final_order:
        clear_state()
        ranked = [tracks[i] for i in sorter.final_order]
        return jsonify({"done": True, "ranked": ranked})

    if pair is None:
        if sorter.final_order:
            clear_state()
            ranked = [tracks[i] for i in sorter.final_order]
            return jsonify({"done": True, "ranked": ranked})
        return jsonify({"error": "Sorting error — no next pair"}), 500

    save_state({"tracks": tracks, "sorter": sorter.to_dict()})

    a, b = pair
    return jsonify({
        "done": False,
        "track_a": {**tracks[a], "index": a},
        "track_b": {**tracks[b], "index": b},
        "progress": sorter.progress(),
        "comp_count": sorter.comp_count,
        "estimated_total": int(len(tracks) * math.log2(len(tracks))) if len(tracks) > 1 else 1,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)