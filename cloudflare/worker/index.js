// Cloudflare Worker — job feedback routing for Timo's job scraper
//
// Routes:
//   GET  /feedback?job_id=X&action=like|pass&sig=HMAC  — quick like/pass from email
//   GET  /rate?job_id=X&sig=HMAC                        — serve mobile rating form
//   POST /rate                                           — submit rating form
//   GET  /poll                                           — pull pending entries (Authorization header)
//   DELETE /poll                                         — clear pulled entries (Authorization header)
//
// Environment bindings (set via wrangler secret / wrangler.toml):
//   CF_WORKER_SECRET  — shared HMAC + poll auth secret
//   FEEDBACK_KV       — KV namespace binding
//
// Verified 2026-05-29. Deploy: wrangler deploy

const KV_TTL = 60 * 60 * 24 * 7; // 7 days in seconds

// ── HMAC helpers ──────────────────────────────────────────────────────────────

async function computeHMAC(secret, payload) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const buf = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  return Array.from(new Uint8Array(buf))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

function dailyBucket() {
  return Math.floor(Date.now() / 1000 / 86400);
}

async function verifyActionSig(secret, jobId, action, sig) {
  const payload = `${jobId}:${action}:${dailyBucket()}`;
  const expected = await computeHMAC(secret, payload);
  return expected === sig;
}

// ── HTML helpers ──────────────────────────────────────────────────────────────

function thanksPage(action) {
  const emoji = action === "like" ? "✅" : action === "pass" ? "❌" : "📝";
  const msg = action === "like"
    ? "Marked as Interested"
    : action === "pass"
    ? "Marked as Pass"
    : "Rating saved";
  return new Response(
    `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feedback saved</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;display:flex;align-items:center;justify-content:center;
       min-height:100vh;background:#f8fafc;color:#1e293b}
  .card{background:#fff;border-radius:12px;padding:32px 24px;text-align:center;
        max-width:320px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
  .emoji{font-size:48px;margin-bottom:16px}
  h2{margin:0 0 8px;font-size:20px}
  p{color:#64748b;font-size:14px;margin:0}
</style></head><body>
<div class="card">
  <div class="emoji">${emoji}</div>
  <h2>${msg}</h2>
  <p>Feedback syncs to your machine within the hour.</p>
</div>
</body></html>`,
    { status: 200, headers: { "Content-Type": "text/html;charset=UTF-8" } }
  );
}

function ratePage(jobId, sig) {
  return new Response(
    `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rate this job</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;padding:20px;background:#f8fafc;color:#1e293b}
  .card{background:#fff;border-radius:12px;padding:24px;max-width:480px;
        margin:auto;box-shadow:0 2px 12px rgba(0,0,0,.08)}
  h2{margin:0 0 20px;font-size:18px}
  label{display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#374151}
  input[type=range]{width:100%;accent-color:#2563eb;cursor:pointer}
  .score-display{font-size:18px;font-weight:bold;margin:6px 0 4px;min-height:28px;color:#1a1a2e}
  textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d1d5db;
           border-radius:6px;font-size:14px;resize:vertical;min-height:70px;margin-top:4px}
  .btn{display:block;width:100%;padding:12px;background:#2563eb;color:#fff;
       border:none;border-radius:6px;font-size:16px;font-weight:600;
       cursor:pointer;margin-top:16px}
  .btn:hover{background:#1d4ed8}
</style></head><body>
<div class="card">
  <h2>Rate this job</h2>
  <form method="POST" action="/rate">
    <input type="hidden" name="job_id" value="${jobId}">
    <input type="hidden" name="sig" value="${sig}">

    <label for="score">How relevant is this job? <span id="score-display" class="score-display"></span></label>
    <input type="range" id="score" name="score" min="1" max="10" value="5"
           oninput="updateScore(this.value)">

    <label for="reason" style="margin-top:14px">Optional note</label>
    <textarea id="reason" name="reason" placeholder="Why is it a good or bad match?"></textarea>

    <button type="submit" class="btn">Save rating</button>
  </form>
</div>
<script>
function updateScore(v) {
  v = parseInt(v);
  var labels = ['','Not relevant','Not relevant','Not relevant',
                'Possibly relevant','Possibly relevant','Possibly relevant',
                'Strong match','Strong match','Strong match','Strong match'];
  document.getElementById('score-display').textContent = v + '/10 — ' + labels[v];
}
updateScore(5);
</script>
</body></html>`,
    { status: 200, headers: { "Content-Type": "text/html;charset=UTF-8" } }
  );
}

function errorPage(status, msg) {
  return new Response(
    `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error</title></head><body style="font-family:sans-serif;padding:20px;text-align:center">
<h2>${status}</h2><p>${msg}</p></body></html>`,
    { status, headers: { "Content-Type": "text/html;charset=UTF-8" } }
  );
}

// ── Request handler ───────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const params = url.searchParams;
    const secret = env.CF_WORKER_SECRET;
    const kv = env.FEEDBACK_KV;

    // GET /feedback — quick like/pass from email button
    if (request.method === "GET" && path === "/feedback") {
      const jobId = params.get("job_id") || "";
      const action = params.get("action") || "";
      const sig = params.get("sig") || "";

      if (!jobId || !["like", "pass"].includes(action) || !sig) {
        return errorPage(400, "Missing or invalid parameters.");
      }
      if (!(await verifyActionSig(secret, jobId, action, sig))) {
        return errorPage(403, "Link expired or invalid. Use a fresh email.");
      }

      await kv.put(
        `feedback:${jobId}`,
        JSON.stringify({ job_id: jobId, action, score: null, reason: "", ts: new Date().toISOString() }),
        { expirationTtl: KV_TTL }
      );
      return thanksPage(action);
    }

    // GET /rate — serve mobile rating form
    if (request.method === "GET" && path === "/rate") {
      const jobId = params.get("job_id") || "";
      const sig = params.get("sig") || "";

      if (!jobId || !sig) {
        return errorPage(400, "Missing parameters.");
      }
      // Rate links are signed as action="rate"
      if (!(await verifyActionSig(secret, jobId, "rate", sig))) {
        return errorPage(403, "Link expired or invalid. Use a fresh email.");
      }
      return ratePage(jobId, sig);
    }

    // POST /rate — submit rating form
    if (request.method === "POST" && path === "/rate") {
      let body;
      try {
        body = await request.formData();
      } catch {
        return errorPage(400, "Invalid form data.");
      }
      const jobId = body.get("job_id") || "";
      const sig = body.get("sig") || "";
      const scoreRaw = parseInt(body.get("score") || "5", 10);
      const reason = (body.get("reason") || "").trim().slice(0, 500);
      const score = Math.max(1, Math.min(10, isNaN(scoreRaw) ? 5 : scoreRaw));
      const action = score >= 7 ? "like" : "pass";

      if (!jobId || !sig) {
        return errorPage(400, "Missing parameters.");
      }
      if (!(await verifyActionSig(secret, jobId, "rate", sig))) {
        return errorPage(403, "Link expired or invalid. Use a fresh email.");
      }

      await kv.put(
        `feedback:${jobId}`,
        JSON.stringify({ job_id: jobId, action, score, reason, ts: new Date().toISOString() }),
        { expirationTtl: KV_TTL }
      );
      return thanksPage("rate");
    }

    // GET /poll — return all pending KV entries (requires Authorization header)
    if (request.method === "GET" && path === "/poll") {
      const auth = request.headers.get("Authorization") || "";
      if (auth !== `Bearer ${secret}`) {
        return new Response("Unauthorized", { status: 401 });
      }

      const list = await kv.list({ prefix: "feedback:" });
      const entries = [];
      for (const key of list.keys) {
        const val = await kv.get(key.name, { type: "json" });
        if (val) entries.push(val);
      }
      return new Response(JSON.stringify(entries), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    // DELETE /poll — remove all feedback entries after successful sync
    if (request.method === "DELETE" && path === "/poll") {
      const auth = request.headers.get("Authorization") || "";
      if (auth !== `Bearer ${secret}`) {
        return new Response("Unauthorized", { status: 401 });
      }

      const list = await kv.list({ prefix: "feedback:" });
      await Promise.all(list.keys.map(k => kv.delete(k.name)));
      return new Response(JSON.stringify({ deleted: list.keys.length }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    return errorPage(404, "Not found.");
  },
};
