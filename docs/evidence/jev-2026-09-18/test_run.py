"""First live test of Jev. Made-up emails only. Key is read from ~/.env and never printed."""
import json
import os
import time
import urllib.error
import urllib.request

URL = "https://api.typesafe.ai/v1/systemone"


def load_key():
    with open(os.path.expanduser("~/.env")) as fh:
        for line in fh:
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("TYPESAFE_API_KEY not found in ~/.env")


EMAILS = [
    {
        "id": "swim-meet",
        "from": "coach@riverside-swim.example",
        "subject": "Saturday meet: warm-up moved to 7:15, reply by tonight",
        "body": "Parents, the pool changed our slot. Warm-up is now 7:15 not 8:30. "
                "Please confirm tonight if your swimmer is still coming so I can fix the relay teams.",
    },
    {
        "id": "newsletter",
        "from": "deals@gadget-mart.example",
        "subject": "48 HOURS ONLY: 30% off everything",
        "body": "Our biggest sale of the season ends Sunday. Shop now and save on headphones, chargers and more.",
    },
    {
        "id": "invoice",
        "from": "billing@cloud-host.example",
        "subject": "Payment failed for your September invoice",
        "body": "We could not charge the card on file. Your services stay active for 5 more days. "
                "Update your payment method to avoid suspension.",
    },
    {
        "id": "vague",
        "from": "jordan@old-colleague.example",
        "subject": "quick thought",
        "body": "Hey, saw something that made me think of that project we talked about. No rush. Coffee sometime?",
    },
]

QUESTIONS = {
    "category": {
        "type": "choice",
        "instructions": "What kind of email is this for the recipient, a solo business owner and parent?",
        "criteria": {
            "family": "About the recipient's child, school, or sports",
            "money": "Bills, payments, invoices, account problems",
            "work": "Clients, projects, professional contacts",
            "promo": "Marketing, sales, newsletters",
            "none_of_these": "Does not fit any other option",
        },
    },
    "needs_reply_today": {
        "type": "noul",
        "instructions": "Does the sender need a reply or action from the recipient within 24 hours?",
        "criteria": {
            "true": "A deadline or request falls within the next day",
            "false": "No deadline, or the deadline is more than a day away",
        },
    },
    "importance": {
        "type": "score",
        "instructions": "How important is this email for the recipient's morning briefing?",
        "criteria": [
            "Safe to ignore: bulk mail with no personal stake",
            "Low: personal or relevant, but nothing is lost by waiting a week",
            "Medium: should be handled this week",
            "High: real cost or a missed event if not handled in a day or two",
        ],
    },
}


def ask(key, state):
    body = json.dumps({"state": state, "model": "jev-latest", "questions": QUESTIONS}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "jev-test/0.1",
    })
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return {"error": err.code, "detail": err.read().decode()[:500]}, time.perf_counter() - start
    return data, time.perf_counter() - start


def main():
    key = load_key()
    results = []
    for mail in EMAILS:
        state = {k: mail[k] for k in ("from", "subject", "body")}
        data, secs = ask(key, state)
        results.append({"id": mail["id"], "seconds": round(secs, 3), "response": data})
        print(f"\n=== {mail['id']}  ({secs:.2f}s)")
        if "error" in data:
            print(data)
            continue
        a = data["answers"]
        c = a["category"]
        print(f"category: {c['choice']}  conf={c['confidence']:.2f}  probs={ {k: round(v, 2) for k, v in c['probabilities'].items()} }")
        print(f"needs_reply_today: {a['needs_reply_today']['noul']:.2f}")
        s = a["importance"]
        print(f"importance: {s['score']:.2f}/3  conf={s['confidence']:.2f}  probs={ {k: round(v, 2) for k, v in s['probabilities'].items()} }")
        print(f"usage: {data.get('usage')}  model: {data.get('model')}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_run_results.json")
    with open(out, "w") as fh:
        json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
