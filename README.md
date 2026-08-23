# 4th & Inches — league site

Static landing page for the league. No build step — it's plain HTML/CSS/JS,
so GitHub Pages can serve it as-is.

## Files

- `index.html` — the page
- `style.css` — all styling
- `script.js` — currently just notes/stubs for the future Sleeper data pull
- `assets/` — drop your league logo here as `logo.png` (referenced in the header; if it's missing the site just hides that spot, nothing breaks)

## Getting it live on GitHub Pages

Since your GitHub username is `4thNInches`, the clean URL
(`4thandinches.github.io`) isn't available to you directly — GitHub Pages
user sites have to live in a repo named exactly `<username>.github.io`. You've
got two reasonable paths:

**Option A — user site at `4thNInches.github.io` (simplest)**
1. Create a new repo on GitHub named exactly `4thNInches.github.io`.
2. Push these files to the `main` branch, at the repo root.
3. In the repo's **Settings → Pages**, set the source to "Deploy from a
   branch," branch `main`, folder `/ (root)`.
4. Site is live at `https://4thNInches.github.io/` within a minute or two.

**Option B — project site, custom-branded URL later**
1. Create a repo with any name, e.g. `league-site`.
2. Same Pages setup as above — it'll be live at
   `https://4thNInches.github.io/league-site/`.
3. If you later buy the domain `4thandinches.com` (or similar), add a
   `CNAME` file with that domain and point its DNS at GitHub Pages — then
   it doesn't matter what your GitHub username is.

Option A gets you the shortest URL with zero cost, so that's the one I'd
start with.

## Local preview

No server needed — just open `index.html` in a browser. If you want it to
behave exactly like it will once fetch() calls to the Sleeper API are added,
run a tiny local server instead of opening the file directly:

```
cd 4thandinches-site
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.

## Next steps (not done yet)

- Drop `logo.png` into `assets/`
- Get your Sleeper league ID (Sleeper app → League → Settings, or from the
  URL when viewing the league on sleeper.com) and wire it into `script.js`
- Standings, stats, and history sections are currently placeholder cards —
  those are where your Yahoo-era Python output should eventually land, once
  it's re-pointed at Sleeper's API instead of Yahoo's
