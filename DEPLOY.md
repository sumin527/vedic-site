# 배포 가이드 — GitHub + Cloudflare + Railway

## 아키텍처

```
GitHub repo (site/)
├── web/   → Cloudflare Pages (정적, 무료)
└── api/   → Railway (Python FastAPI, 월 $5)
```

Cloudflare Workers로는 Python 엔진(pyswisseph, C 확장)을 실행할 수 없어서
API는 별도 호스팅한다. 검증된 엔진을 그대로 쓰는 게 정확성 면에서 안전하다.

## 1. GitHub에 푸시

```bash
cd site/
gh repo create vedic-site --public --source=. --push
# 또는 GitHub 웹에서 repo 생성 후:
# git remote add origin https://github.com/<계정>/vedic-site.git
# git push -u origin main
```

## 2. Cloudflare Pages (프론트)

1. Cloudflare 대시보드 → Workers & Pages → Create → Pages → Connect to Git
2. repo `vedic-site` 선택
3. Build settings:
   - Framework preset: None
   - Build command: (비워둠)
   - Build output directory: `web`
4. Deploy → `https://vedic-site.pages.dev` 발급

## 3. Railway (API)

1. railway.app → New Project → Deploy from GitHub repo → `vedic-site`
2. Settings → Root Directory: `api`
3. Variables: `PORT`는 자동. 별도 설정 불필요 (railway.json이 startCommand 지정)
4. Deploy 후 도메인 발급: Settings → Domains → Generate Domain
   예: `https://vedic-site-api.up.railway.app`
5. 헬스체크: `https://<도메인>/api/health` → `{"ok":true}`

## 4. 프론트-API 연결

`web/config.js` 수정 후 푸시 (Pages가 자동 재배포):

```js
window.API_BASE = "https://<railway 도메인>";
```

## 5. 커스텀 도메인 (선택)

- Cloudflare → 도메인 DNS → Pages에 커스텀 도메인 연결
- API는 `api.도메인` 서브도메인으로 Railway에 연결

## 비용

| 항목 | 비용 |
|---|---|
| Cloudflare Pages | 무료 |
| Railway | 월 $5 (Hobby) |
| 도메인 | 연 ~$10 |

## 주의

- 상용 트래픽 전 Swiss Ephemeris 라이선스 결정 (상용 CHF 750 or MIT 대안)
- 현재 rate limit: IP당 분당 60회 (main.py)
