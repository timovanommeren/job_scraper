// Cloudflare Worker — job feedback routing for Timo's job scraper
//
// Routes:
//   GET  /feedback?job_id=X&score=N&sig=HMAC              — rating row pill tap → POSTs to Flask
//   GET  /feedback?job_id=X&action=like|pass&sig=HMAC     — legacy like/pass → POSTs to Flask
//   GET  /feedback?suggestion_id=N&action=skip_suggestion&sig — skip suggestion → POSTs to Flask
//   GET  /survey?job_id=X&sig=HMAC&title=...&org=...       — serve mobile criteria form
//   POST /survey                                            — submit criteria form → POSTs to Flask
//   GET  /rate?job_id=X&sig=HMAC                           — backward compat: renders survey form
//
// Environment bindings:
//   CF_WORKER_SECRET  — shared HMAC + API auth secret (wrangler secret put CF_WORKER_SECRET)
//   FLASK_API_URL     — Cloudflare Tunnel URL proxying to Flask (set in wrangler.toml [vars])
//
// No KV namespace — all feedback is forwarded directly to Flask via FLASK_API_URL.
// Verified 2026-06-04. Deploy: wrangler deploy (runs generate_worker_form.py via [build])

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

function weeklyBucket() {
  return Math.floor(Date.now() / 1000 / 604800);
}

async function verifyActionSig(secret, jobId, action, sig) {
  const payload = `${jobId}:${action}:${weeklyBucket()}`;
  const expected = await computeHMAC(secret, payload);
  return expected === sig;
}

// ── Flask API helper ──────────────────────────────────────────────────────────

async function postToFlask(flaskUrl, secret, path, body) {
  const resp = await fetch(`${flaskUrl}${path}`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${secret}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return resp;
}

// ── HTML helpers ──────────────────────────────────────────────────────────────

function thanksPage(action, score) {
  let emoji, msg;
  if (typeof score === "number") {
    if (score >= 7) { emoji = "✅"; msg = `Marked as Interested · ${score}/10`; }
    else if (score >= 5) { emoji = "📝"; msg = `Rating saved · ${score}/10`; }
    else { emoji = "❌"; msg = `Marked as Pass · ${score}/10`; }
  } else {
    emoji = action === "like" ? "✅" : action === "pass" ? "❌" : "📝";
    msg = action === "like" ? "Marked as Interested"
        : action === "pass" ? "Marked as Pass"
        : "Rating saved";
  }
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
  <p>Feedback saved to your scoring pipeline.</p>
</div>
</body></html>`,
    { status: 200, headers: { "Content-Type": "text/html;charset=UTF-8" } }
  );
}

function surveyThanksPage() {
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
        max-width:360px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
  .emoji{font-size:48px;margin-bottom:16px}
  h2{margin:0 0 8px;font-size:20px}
  p{color:#64748b;font-size:14px;margin:0}
</style></head><body>
<div class="card">
  <div class="emoji">✅</div>
  <h2>Feedback saved</h2>
  <p>Your detailed rating is saved to the scoring pipeline.</p>
</div>
</body></html>`,
    { status: 200, headers: { "Content-Type": "text/html;charset=UTF-8" } }
  );
}

function surveyErrorPage() {
  return new Response(
    `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;display:flex;align-items:center;justify-content:center;
       min-height:100vh;background:#f8fafc;color:#1e293b}
  .card{background:#fff;border-radius:12px;padding:32px 24px;text-align:center;
        max-width:360px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
  .emoji{font-size:48px;margin-bottom:16px}
  h2{margin:0 0 8px;font-size:20px}
  p{color:#64748b;font-size:14px;margin:0}
</style></head><body>
<div class="card">
  <div class="emoji">⚠️</div>
  <h2>Something went wrong</h2>
  <p>Try again or open <strong>localhost:5001</strong> to rate this job.</p>
</div>
</body></html>`,
    { status: 500, headers: { "Content-Type": "text/html;charset=UTF-8" } }
  );
}

function expiredPage() {
  return new Response(
    `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link expired</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;display:flex;align-items:center;justify-content:center;
       min-height:100vh;background:#f8fafc;color:#1e293b}
  .card{background:#fff;border-radius:12px;padding:32px 24px;text-align:center;
        max-width:360px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
  .emoji{font-size:48px;margin-bottom:16px}
  h2{margin:0 0 8px;font-size:20px}
  p{color:#64748b;font-size:14px;margin:0}
</style></head><body>
<div class="card">
  <div class="emoji">⏰</div>
  <h2>Link expired</h2>
  <p>Open <strong>localhost:5001</strong> to rate this job.</p>
</div>
</body></html>`,
    { status: 403, headers: { "Content-Type": "text/html;charset=UTF-8" } }
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

function surveyPage(jobId, sig, title, org, originalAction) {
  const jobHeader = title
    ? `<div class="job-header">
        <div class="job-title">${title}</div>
        ${org ? `<div class="job-org">🏢 ${org}</div>` : ""}
       </div>`
    : "";

  // GENERATED_CRITERIA_START — edit config/criteria.yaml + run scripts/generate_worker_form.py
  const criteriaSliderHtml = `
      <div class="criterion-row">
        <div class="criterion-header">
          <span class="criterion-label">Topic relevance</span>
          <span class="criterion-val" id="val-topic_fit">3</span>
        </div>
        <input type="range" name="criteria_topic_fit" min="1" max="5" value="3"
               aria-label="Topic relevance" aria-valuemin="1" aria-valuemax="5" aria-valuenow="3"
               oninput="updateCriteria('topic_fit', this.value)">
        <div class="criterion-hints"><span>1 — Wrong research area</span><span>5 — Core interest area</span></div>
      </div>
      <div class="criterion-row">
        <div class="criterion-header">
          <span class="criterion-label">Methods match</span>
          <span class="criterion-val" id="val-methods_fit">3</span>
        </div>
        <input type="range" name="criteria_methods_fit" min="1" max="5" value="3"
               aria-label="Methods match" aria-valuemin="1" aria-valuemax="5" aria-valuenow="3"
               oninput="updateCriteria('methods_fit', this.value)">
        <div class="criterion-hints"><span>1 — Methods I don't use</span><span>5 — Perfect methods match</span></div>
      </div>
      <div class="criterion-row">
        <div class="criterion-header">
          <span class="criterion-label">Organization appeal</span>
          <span class="criterion-val" id="val-org_appeal">3</span>
        </div>
        <input type="range" name="criteria_org_appeal" min="1" max="5" value="3"
               aria-label="Organization appeal" aria-valuemin="1" aria-valuemax="5" aria-valuenow="3"
               oninput="updateCriteria('org_appeal', this.value)">
        <div class="criterion-hints"><span>1 — Not interested in this org</span><span>5 — Dream organization</span></div>
      </div>
      <div class="criterion-row">
        <div class="criterion-header">
          <span class="criterion-label">Career stage fit</span>
          <span class="criterion-val" id="val-career_fit">3</span>
        </div>
        <input type="range" name="criteria_career_fit" min="1" max="5" value="3"
               aria-label="Career stage fit" aria-valuemin="1" aria-valuemax="5" aria-valuenow="3"
               oninput="updateCriteria('career_fit', this.value)">
        <div class="criterion-hints"><span>1 — Wrong level (e.g. postdoc)</span><span>5 — Perfect career stage</span></div>
      </div>
      <div class="criterion-row">
        <div class="criterion-header">
          <span class="criterion-label">Location</span>
          <span class="criterion-val" id="val-location_fit">3</span>
        </div>
        <input type="range" name="criteria_location_fit" min="1" max="5" value="3"
               aria-label="Location" aria-valuemin="1" aria-valuemax="5" aria-valuenow="3"
               oninput="updateCriteria('location_fit', this.value)">
        <div class="criterion-hints"><span>1 — Outside EU / unacceptable</span><span>5 — Ideal location</span></div>
      </div>
  `;
  const criteriaKeys = ["topic_fit", "methods_fit", "org_appeal", "career_fit", "location_fit"];
  // GENERATED_CRITERIA_END

  return `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rate this job</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;padding:20px;background:#f8fafc;color:#1e293b}
  .card{background:#fff;border-radius:12px;padding:24px;max-width:480px;
        margin:auto;box-shadow:0 2px 12px rgba(0,0,0,.08)}
  .job-header{background:#f1f5f9;border-radius:8px;padding:12px 14px;margin-bottom:20px}
  .job-title{font-size:15px;font-weight:600;color:#1e293b;margin:0 0 4px}
  .job-org{font-size:13px;color:#64748b;margin:0}
  h2{margin:0 0 16px;font-size:18px;color:#1e293b}
  .criterion-row{margin-bottom:18px}
  .criterion-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
  .criterion-label{font-size:14px;font-weight:600;color:#374151}
  .criterion-val{font-size:14px;font-weight:bold;color:#2563eb}
  input[type=range]{-webkit-appearance:none;width:100%;height:6px;border-radius:3px;
                    background:#e2e8f0;outline:none;cursor:pointer;
                    padding:12px 0;box-sizing:content-box;display:block}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:32px;height:32px;
    border-radius:50%;background:#2563eb;cursor:pointer}
  input[type=range]::-moz-range-thumb{width:32px;height:32px;border-radius:50%;
    background:#2563eb;cursor:pointer;border:none}
  .criterion-hints{display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin-top:2px}
  .derived-score{font-size:16px;font-weight:bold;color:#1a1a2e;margin:16px 0 8px}
  .applied-row{display:flex;align-items:center;min-height:44px;padding:4px 0;
               margin-bottom:12px;cursor:pointer;gap:10px}
  .applied-row input[type=checkbox]{width:20px;height:20px;cursor:pointer;
                                    accent-color:#16a34a;flex-shrink:0}
  .applied-row span{font-size:14px;color:#374151}
  label.comment-label{display:block;font-size:14px;font-weight:600;color:#374151;margin-bottom:4px}
  textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d1d5db;
           border-radius:6px;font-size:14px;resize:vertical;min-height:70px;margin-top:4px}
  .btn{display:block;width:100%;min-height:48px;padding:14px;background:#2563eb;color:#fff;
       border:none;border-radius:6px;font-size:16px;font-weight:600;cursor:pointer;margin-top:16px}
  .btn:hover{background:#1d4ed8}
</style></head><body>
<div class="card">
  ${jobHeader}
  <h2>Rate this job</h2>
  <form method="POST" action="/survey" id="surveyForm">
    <input type="hidden" name="job_id" value="${jobId}">
    <input type="hidden" name="sig" value="${sig}">
    <input type="hidden" name="original_action" value="${originalAction}">
    ${criteriaSliderHtml}
    <div class="derived-score" id="derived-score">Score: 6 / 10</div>
    <label class="applied-row">
      <input type="checkbox" name="applied" value="1" id="applied-check">
      <span>I applied for this job ✅</span>
    </label>
    <label class="comment-label" for="comment">Optional note</label>
    <textarea id="comment" name="comment" placeholder="Anything else?"></textarea>
    <button type="submit" class="btn">Submit feedback</button>
  </form>
</div>
<script>
var criteriaKeys = ${JSON.stringify(criteriaKeys)};
function updateCriteria(key, val) {
  val = parseInt(val);
  document.getElementById('val-' + key).textContent = val;
  var el = document.querySelector('[name="criteria_' + key + '"]');
  if (el) el.setAttribute('aria-valuenow', val);
  updateScore();
}
function updateScore() {
  var total = 0;
  criteriaKeys.forEach(function(k) {
    var el = document.querySelector('[name="criteria_' + k + '"]');
    if (el) total += parseInt(el.value);
  });
  var score = Math.round(total / criteriaKeys.length * 2);
  document.getElementById('derived-score').textContent = 'Score: ' + score + ' / 10';
}
updateScore();
</script>
</body></html>`;
}

// ── Request handler ───────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const params = url.searchParams;
    const secret = env.CF_WORKER_SECRET;
    const flaskUrl = (env.FLASK_API_URL || "").replace(/\/$/, "");

    // GET /feedback — rating row pill tap, legacy like/pass, or source suggestion skip
    if (request.method === "GET" && path === "/feedback") {
      const jobId = params.get("job_id") || "";
      const sig = params.get("sig") || "";
      const scoreRaw = params.get("score");
      const action = params.get("action") || "";
      const suggestionId = params.get("suggestion_id") || "";

      // ── Source suggestion skip ────────────────────────────────────────────────
      if (action === "skip_suggestion" && suggestionId) {
        if (!sig) return errorPage(400, "Missing parameters.");
        if (!(await verifyActionSig(secret, suggestionId, "skip_suggestion", sig))) {
          return expiredPage();
        }
        if (!flaskUrl) return errorPage(500, "Flask API URL not configured.");
        try {
          await postToFlask(flaskUrl, secret, "/api/v1/skip-suggestion",
            { suggestion_id: parseInt(suggestionId, 10) });
        } catch (e) {
          console.error(`[skip-suggestion] Flask call failed: ${e}`);
        }
        return new Response(
          `<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skipped</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;
display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f8fafc}
.card{background:#fff;border-radius:12px;padding:32px 24px;text-align:center;max-width:320px;
box-shadow:0 2px 12px rgba(0,0,0,.08)}.emoji{font-size:48px;margin-bottom:16px}
h2{margin:0 0 8px;font-size:20px}p{color:#64748b;font-size:14px;margin:0}</style>
</head><body><div class="card">
<div class="emoji">🚫</div>
<h2>Suggestion dismissed</h2>
<p>You won't see this organisation suggested again.</p>
</div></body></html>`,
          { status: 200, headers: { "Content-Type": "text/html;charset=UTF-8" } }
        );
      }

      // ── Job rating (rating row pill or legacy like/pass button) ───────────────
      let score = null;
      let resolvedAction = action;

      if (scoreRaw !== null) {
        score = Math.max(1, Math.min(10, parseInt(scoreRaw, 10) || 5));
        resolvedAction = score >= 7 ? "like" : "pass";
        if (!jobId || !sig) return errorPage(400, "Missing parameters.");
        if (!(await verifyActionSig(secret, jobId, "rate", sig))) {
          return expiredPage();
        }
      } else {
        if (!jobId || !["like", "pass"].includes(resolvedAction) || !sig) {
          return errorPage(400, "Missing or invalid parameters.");
        }
        if (!(await verifyActionSig(secret, jobId, resolvedAction, sig))) {
          return expiredPage();
        }
      }

      if (!flaskUrl) return errorPage(500, "Flask API URL not configured.");
      try {
        const resp = await postToFlask(flaskUrl, secret, "/api/v1/feedback",
          { job_id: jobId, action: resolvedAction, score });
        if (!resp.ok) {
          console.error(`[feedback] Flask error: ${resp.status}`);
        }
      } catch (e) {
        console.error(`[feedback] Flask call failed: ${e}`);
      }
      return thanksPage(resolvedAction, score);
    }

    // GET /survey or GET /rate — serve mobile criteria form
    if (request.method === "GET" && (path === "/survey" || path === "/rate")) {
      const jobId = params.get("job_id") || "";
      const sig = params.get("sig") || "";
      const title = decodeURIComponent(params.get("title") || "");
      const org = decodeURIComponent(params.get("org") || "");
      const originalAction = path === "/rate" ? "rate" : "survey";

      if (!jobId || !sig) return errorPage(400, "Missing parameters.");
      if (!(await verifyActionSig(secret, jobId, originalAction, sig))) {
        return expiredPage();
      }
      return new Response(surveyPage(jobId, sig, title, org, originalAction),
        { status: 200, headers: { "Content-Type": "text/html;charset=UTF-8" } });
    }

    // POST /survey — submit criteria form
    if (request.method === "POST" && path === "/survey") {
      let body;
      try {
        body = await request.formData();
      } catch {
        return errorPage(400, "Invalid form data.");
      }

      const jobId = body.get("job_id") || "";
      const sig = body.get("sig") || "";
      const originalAction = body.get("original_action") || "survey";
      const comment = (body.get("comment") || "").trim().slice(0, 1000);
      const applied = body.get("applied") === "1";

      if (!jobId || !sig) return errorPage(400, "Missing parameters.");
      if (!["survey", "rate"].includes(originalAction)) {
        return errorPage(400, "Invalid action.");
      }
      if (!(await verifyActionSig(secret, jobId, originalAction, sig))) {
        return expiredPage();
      }

      // Parse criteria values (1–5 each)
      const criteria = {};
      const keys = ["topic_fit", "methods_fit", "org_appeal", "career_fit", "location_fit"];
      for (const k of keys) {
        const raw = parseInt(body.get(`criteria_${k}`) || "3", 10);
        criteria[k] = Math.max(1, Math.min(5, isNaN(raw) ? 3 : raw));
      }

      // Derive score: avg(criteria) × 2 → 2–10 range
      const avg = Object.values(criteria).reduce((s, v) => s + v, 0) / keys.length;
      const derivedScore = applied ? 10 : Math.max(1, Math.min(10, Math.round(avg * 2)));
      const action = applied ? "applied" : (derivedScore >= 7 ? "like" : "pass");

      if (!flaskUrl) return surveyErrorPage();
      try {
        const resp = await postToFlask(flaskUrl, secret, "/api/v1/feedback", {
          job_id: jobId,
          action,
          derived_score: derivedScore,
          criteria,
          comment,
        });
        if (!resp.ok) {
          console.error(`[survey POST] Flask error: ${resp.status}`);
          return surveyErrorPage();
        }
      } catch (e) {
        console.error(`[survey POST] Flask call failed: ${e}`);
        return surveyErrorPage();
      }
      return surveyThanksPage();
    }

    return errorPage(404, "Not found.");
  },
};
