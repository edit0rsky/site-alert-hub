# site-alert-hub

공개 게시판 목록을 10분마다 확인하고, 새 게시글의 사이트·게시판·제목·링크를 Telegram으로 보내는 운영용 프로젝트입니다. 게시글 상세 페이지는 요청하지 않으며, 본문 분석이나 요약·LLM API는 사용하지 않습니다.

GitHub Actions 예약 실행은 실시간 스트리밍이 아니라 주기적 감지 방식입니다. GitHub의 스케줄 지연이 있을 수 있으므로 Telegram 알림 시각은 게시글 게시 시각과 같다고 가정하지 않습니다.

## 현재 등록된 게시판

`config/source-pages.yml`에는 다음 7개 목록 페이지가 설정되어 있습니다. 첫 실행은 `baseline`이므로 현재 목록의 기존 글은 저장만 하고 Telegram으로 보내지 않습니다.

| 분류 | 사이트 | 게시판 | URL |
| --- | --- | --- | --- |
| 정부·공공 | 성북구청 | 새소식 | <https://www.sb.go.kr/www/selectBbsNttList.do?bbsNo=41&key=6350> |
| 정부·공공 | 서울시청 | 서울소식 | <https://www.seoul.go.kr/realmnews/in/list.do> |
| 정부·공공 | 서울시청 | 행사·축제 | <https://www.seoul.go.kr/thismteventfstvl/list.do> |
| 정부·공공 | 서울시청 | 이벤트·신청 | <https://www.seoul.go.kr/eventreqst/list.do> |
| 카페 이벤트 | 메가MGC커피 | 이벤트 | <https://www.mega-mgccoffee.com/bbs/?bbs_category=3> |
| 카페 이벤트 | 더벤티 | 이벤트 | <https://theventi.co.kr/new2022/news/event.html> |
| 카페 이벤트 | 공차 | 이벤트 | <https://www.gong-cha.co.kr/brand/content/eventlist> |

선택자는 목록 HTML의 현재 구조를 기준으로 작성했습니다. 사이트 구조가 바뀌어 선택자 결과가 0개가 되면 `parser_selector_mismatch`로 실패 기록을 남기며, 글이 없는 것으로 처리하지 않습니다. 실제 운영 전 각 사이트의 robots.txt와 이용약관, 자동 수집 허용 범위를 확인하세요.

## 구조

```text
GitHub Actions → Python worker → Supabase PostgreSQL → Telegram Bot
                                      ↑
                              React 운영 대시보드
```

- `worker/src/site_alert/parsers`: Parser Registry와 Generic HTML parser
- `worker/src/site_alert/services`: baseline, 중복 방지, delivery claim, retry pipeline
- `worker/src/site_alert/repositories`: Supabase adapter와 테스트용 in-memory adapter
- `worker/src/site_alert/clients`: robots.txt/HTTP 및 Telegram API adapter
- `supabase/migrations`: 테이블, unique constraint, RLS, atomic delivery claim RPC
- `dashboard`: Supabase Auth 기반 한국어 운영 화면
- `.github/workflows`: CI, 10분 주기 크롤링, GitHub Pages 배포

## 로컬 실행

Python 3.12 이상과 Node.js 20 이상을 사용합니다.

```bash
cp .env.example .env
python3 -m venv worker/.venv
worker/.venv/bin/pip install -e 'worker[dev]'
# JS 렌더링 게시판을 쓸 때만 추가:
# worker/.venv/bin/pip install -e 'worker[dev,browser]'
# worker/.venv/bin/playwright install chromium
```

Supabase SQL Editor 또는 Supabase CLI로 `supabase/migrations/202607130001_initial.sql`을 적용한 뒤 `.env`에 다음 값을 넣습니다.

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY       # worker 전용. 브라우저에 절대 넣지 않음
TELEGRAM_BOTS_JSON
SOURCE_PAGES_CONFIG=config/source-pages.yml
```

`TELEGRAM_BOTS_JSON` 예시:

```json
{
  "government": {"token": "123456:replace-me", "chat_id": "@government_notice_channel"},
  "cafe": {"token": "123456:replace-me", "chat_id": "@cafe_event_channel"}
}
```

Supabase Auth에서 운영자 계정을 만든 다음 SQL Editor에서 해당 사용자를 등록합니다.

```sql
insert into public.admin_users (user_id)
select id from auth.users where email = 'admin@example.com';
```

최초 baseline과 주기 실행:

```bash
python -m site_alert.main baseline --source-page-id seongbuk_notice
python -m site_alert.main crawl
python -m site_alert.main crawl --dry-run
python -m site_alert.main crawl --source-page-id mega_event
python -m site_alert.main retry --delivery-id DELIVERY_UUID
```

`baseline` 명령은 기존 전송 이력을 삭제하지 않습니다. 이후 새 글만 `pending`으로 만들어 Telegram으로 보냅니다.

## 대시보드

```bash
cd dashboard
cp ../.env.example .env.local
# .env.local에는 VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY만 입력
npm install
npm run dev
```

대시보드에는 오늘의 신규 글/성공/실패/재전송 대기 건수, 게시글 필터, 게시판 상태, 전송 시도 이력과 실패 delivery의 `재전송 요청` 버튼이 있습니다. 일반 회원가입 화면은 제공하지 않습니다.

## GitHub Secrets

Worker Actions:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TELEGRAM_BOTS_JSON`

Pages build:

- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`

Service Role Key와 Telegram token은 소스·DB·프론트엔드·로그에 저장하지 않습니다. Telegram API 오류에도 token을 포함한 URL을 출력하지 않습니다.

## 중복 방지와 실패 복구

게시글 식별은 `external_id → normalized_url → source_page_id와 identity의 SHA-256 article_key` 순서입니다. URL은 상대경로, fragment, 추적 query, trailing slash, query 정렬을 정규화합니다.

전송 직전 DB RPC `claim_delivery`가 row lock과 unique constraint를 이용해 하나의 Worker만 `sending` 상태를 획득합니다. 따라서 Actions가 겹쳐 실행되어도 이미 `sent`인 delivery는 다시 보내지 않습니다. `sending`으로 15분 이상 남은 작업은 다음 실행에서 복구됩니다.

재시도 대상은 네트워크 오류, timeout, HTTP 429/5xx와 Telegram의 일시적 오류입니다. 재시도 시각과 시도 횟수를 저장하고 최대 시도 횟수 이후 `dead_letter`로 남깁니다. 성공 기록과 모든 `delivery_attempts`는 보존합니다.

## 검증 명령

```bash
worker/.venv/bin/ruff check worker/src worker/tests
worker/.venv/bin/ruff format --check worker/src worker/tests
worker/.venv/bin/pytest worker/tests
cd dashboard && npm run typecheck && npm run build
```

테스트는 외부 사이트·Supabase·Telegram에 직접 연결하지 않고 HTML fixture, in-memory repository, mock Telegram client를 사용합니다. 핵심 worker 커버리지는 80% 이상으로 설정되어 있습니다.

## 실제 사이트 추가 방법

1. `config/source-pages.yml`에 게시판 단위 항목을 추가합니다.
2. 목록 페이지에서만 `item`, `title`, `link` 선택자를 확인합니다. `date`와 `external_id`는 명확히 표시될 때만 설정합니다.
3. 상세 페이지를 호출하지 않는지 dry run으로 확인합니다.
4. Supabase migration 적용 후 worker를 실행해 source page 상태가 성공인지 확인합니다.
5. 별도 Bot profile이 필요하면 `TELEGRAM_BOTS_JSON`에 profile을 추가하고 `bot_profile`에 그 이름을 사용합니다.
6. 첫 운영 실행은 반드시 해당 게시판의 baseline으로 시작합니다.

Generic parser로 처리할 수 없는 사이트는 `worker/src/site_alert/parsers`에 `BoardParser` 구현체를 추가하고 `default_registry()`에 등록하면 됩니다.

## 알려진 범위

- 제공된 7개 URL은 정적 목록 HTML 기준입니다. JavaScript로만 목록이 생성되는 새 사이트는 `render_js: true`와 `worker[browser]` extra, Chromium 설치를 함께 사용합니다.
- 페이지네이션은 첫 목록 페이지에서 제공되는 항목만 확인합니다. 더 오래된 페이지 전체를 탐색하지 않습니다.
- Telegram이 API 요청을 수락한 상태를 `sent`로 저장합니다. 실제 사용자가 읽었는지는 확인하지 않습니다.
- robots.txt로 수집을 금지하면 요청하지 않습니다. robots.txt를 가져오지 못하는 경우에도 요청 주기와 항목 수를 낮게 유지하세요.
