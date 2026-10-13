# 상시 접속 배포 — Cloudflare Workers

## 구조

Workers가 비밀번호 보호된 웹 화면과 API를 제공하고, KV가 계정 데이터와 최대 100개 게시 기록을 보관합니다. 서버 프로세스를 켜둘 필요가 없습니다. Cron은 UTC 00:00, 06:00, 12:00 (KST 09:00, 15:00, 21:00)에 수집합니다. 첫 수집 전에는 빈 화면 상태를 명시합니다. 접속과 데이터 확인 버튼은 수집을 발생시키지 않습니다.

무료 플랜 사용을 목표로 하지만 현재 환경에서 Cloudflare 공식 요금 페이지가 차단되어 최신 무료 할당량은 확인하지 못했습니다. 배포 전 Workers와 KV 무료 플랜 및 Cron 지원·한도를 계정에서 확인하세요. 유료 플랜을 자동으로 선택하지 마세요. 600회 제공사 요청 상한은 비용 상한이 아닙니다. 반응 재관측/재조회와 실패 요청도 포함됩니다.

## 준비

Node.js 22 이상과 Cloudflare 계정 필요. 저장소의 기존 체크아웃 사용, worktree 생성 불필요.

```sh
npm ci
npx wrangler login
npx wrangler kv namespace create DASHBOARD
```

생성 출력의 namespace id를 wrangler.jsonc의 REPLACE_WITH_KV_NAMESPACE_ID 대신 넣습니다. 네임스페이스 ID는 비밀값이 아닙니다.

비밀번호와 API 키는 아래 명령의 프롬프트에서 입력하며, 소스·명령행·Git에 적지 않습니다.

```sh
npx wrangler secret put DASHBOARD_PASSWORD
npx wrangler secret put TWITTERAPI_IO_KEY
```

비밀번호는 최소 16자, ASCII 영문/숫자/기호를 사용하세요. 접속 사용자 이름은 admin입니다. TWITTERAPI_IO_KEY는 TwitterAPI.io에서 재발급한 실제 키를 입력합니다. 클라우드 환경의 프록시용 바인딩을 복사하지 마세요: 플레이스홀더일 수 있고 Cloudflare 런타임에 그대로 이전할 수 없습니다.

## 검증 및 배포

```sh
npm test
npm run check
npm run deploy
```

출력되는 실제 workers.dev 주소에서 로그인하고 화면을 확인합니다. 공개 도메인이라도 모든 화면과 API는 인증이 필요합니다. Secret 미설정 시 화면을 공개하지 않고 503으로 실패합니다. 최초 비밀번호 인증은 브라우저 기본 로그인창을 사용합니다.

배포 직후 첫 수집은 다음 예약 시각에 실행됩니다. Cloudflare Cron 설정 전파에 시간이 걸릴 수 있습니다. 예약 수집 후 세 계정의 팔로워, 최근 글, 수집 시각, provider 오류를 확인해야 온라인 작동 검증이 끝납니다. 예약 수집 로컬 테스트에 사용하는 /__scheduled 엔드포인트는 공개 API가 아닙니다.

## 로컬 개발

```sh
npm run dev
```

.dev.vars (Git에서 제외)에 개발용 DASHBOARD_PASSWORD와 TWITTERAPI_IO_KEY를 넣을 수 있습니다. 실제 키가 있으면 예약 수집 테스트에 비용이 발생합니다. Workers KV는 eventual consistency이며, 다중 기기 동시 기록 시 마지막 저장이 앞선 기록을 덮을 수 있습니다. 현재는 단일 운영자 MVP; 협업 운영에는 트랜잭션 저장소가 필요합니다.

## 아직 없는 기능

AI 추천, 한국 전체 트렌드, 성과 추이, 벤치마크 설정, 텔레그램 알림. 예시 문구와 시간은 초기 운영 템플릿이며 분석 결과가 아닙니다. 원문 삭제/수집 데이터 보관 정책과 데이터 제공사 계약 범위는 실제 운영 전에 결정해야 합니다.
