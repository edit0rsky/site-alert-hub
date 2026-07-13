import { useEffect, useMemo, useState, type FormEvent } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supabase } from "./lib/supabase";
import {
  fetchArticles,
  fetchDeliveries,
  fetchSourcePages,
  fetchSummary,
  requestDeliveryRetry,
  updateSourcePageEnabled,
} from "./lib/api";
import type { Article, ArticleFilters, DeliveryStatus, SourcePage } from "./types";

type SessionUser = { email?: string };

const statusLabel: Record<DeliveryStatus, string> = {
  pending: "대기", sending: "전송 중", sent: "전송 성공", failed: "실패",
  retry_requested: "재전송 요청", dead_letter: "재시도 초과", skipped_initial_sync: "최초 동기화 제외",
};

const formatDate = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "-";

function LoginPage(): JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError("");
    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
    if (signInError) setError("로그인 정보를 확인해 주세요.");
    else navigate("/");
  };
  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark">SAH</div>
        <p className="eyebrow">SITE ALERT HUB</p>
        <h1>운영자 로그인</h1>
        <p className="muted">등록된 운영자 계정으로만 대시보드에 접근할 수 있습니다.</p>
        <form onSubmit={submit} className="stack-form">
          <label>이메일<input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>비밀번호<input type="password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {error && <p className="form-error">{error}</p>}
          <button className="primary-button" type="submit">로그인</button>
        </form>
      </section>
    </main>
  );
}

const navigation = [["/", "현황", "⌂"], ["/articles", "게시글", "◌"], ["/pages", "게시판 관리", "▦"], ["/deliveries", "전송 이력", "↗"]];

function Layout({ user }: { user: SessionUser }): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const logout = async (): Promise<void> => { await supabase.auth.signOut(); navigate("/login"); };
  const heading = navigation.find(([href]) => href === location.pathname)?.[1] ?? "운영 화면";
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><span className="brand-mark small">SAH</span><div><strong>Site Alert</strong><small>운영 콘솔</small></div></div>
        <nav>{navigation.map(([href, label, icon]) => <NavLink key={href} to={href} className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><span>{icon}</span>{label}</NavLink>)}</nav>
        <div className="sidebar-note"><span className="pulse" />10분 주기 감지<br /><small>GitHub Actions 기반</small></div>
      </aside>
      <main className="main-content">
        <header className="topbar"><div><p className="eyebrow">{location.pathname === "/" ? "OVERVIEW" : "OPERATIONS"}</p><h1>{location.pathname === "/" ? "전체 현황" : heading}</h1></div><div className="user-menu"><span>{user.email ?? "운영자"}</span><button className="ghost-button" onClick={logout}>로그아웃</button></div></header>
        <Routes><Route path="/" element={<Overview />} /><Route path="/articles" element={<ArticlesPage />} /><Route path="/pages" element={<PagesPage />} /><Route path="/deliveries" element={<DeliveriesPage />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes>
      </main>
    </div>
  );
}

function StatCard({ label, value, tone, detail }: { label: string; value: number; tone: string; detail: string }): JSX.Element {
  return <div className={`stat-card ${tone}`}><p>{label}</p><strong>{value}</strong><span>{detail}</span></div>;
}

function Overview(): JSX.Element {
  const summary = useQuery({ queryKey: ["summary"], queryFn: fetchSummary });
  const articles = useQuery({ queryKey: ["recent-articles"], queryFn: () => fetchArticles({ category: "", site: "", page: "", status: "", title: "" }) });
  if (summary.isLoading) return <Loading />;
  if (summary.isError) return <ErrorState />;
  const data = summary.data;
  if (!data) return <Loading />;
  return <>
    <section className="hero-strip"><div><span className="status-dot" />모니터링 정상</div><p>게시판별 마지막 성공 시각과 Telegram 전송 상태를 한 곳에서 확인하세요.</p></section>
    <section className="stat-grid"><StatCard label="오늘 신규 게시글" value={data.newArticles} tone="blue" detail="목록 페이지에서 발견" /><StatCard label="오늘 전송 성공" value={data.sent} tone="green" detail="Telegram API 수락" /><StatCard label="전송 실패" value={data.failed} tone="orange" detail="재시도 또는 확인 필요" /><StatCard label="재전송 대기" value={data.retry} tone="purple" detail="다음 실행에서 처리" /></section>
    <section className="section-heading"><div><p className="eyebrow">TODAY</p><h2>운영 상태</h2></div><NavLink to="/pages" className="text-link">게시판 관리 →</NavLink></section>
    <section className="status-grid"><div className="panel status-panel"><div className="status-number">{data.activePages}</div><div><strong>활성 게시판</strong><p>현재 감시 중인 목록 페이지</p></div></div><div className="panel status-panel warning"><div className="status-number">{data.errorPages}</div><div><strong>오류 게시판</strong><p>연속 실패가 기록된 게시판</p></div></div></section>
    <section className="section-heading"><div><p className="eyebrow">LATEST</p><h2>최근 발견 게시글</h2></div><NavLink to="/articles" className="text-link">전체 게시글 →</NavLink></section>
    <ArticleTable articles={(articles.data ?? []).slice(0, 5)} compact />
  </>;
}

function ArticlesPage(): JSX.Element {
  const [filters, setFilters] = useState<ArticleFilters>({ category: "", site: "", page: "", status: "", title: "" });
  const pages = useQuery({ queryKey: ["pages"], queryFn: fetchSourcePages });
  const articles = useQuery({ queryKey: ["articles", filters], queryFn: () => fetchArticles(filters) });
  const sites = useMemo(() => Array.from(new Set((pages.data ?? []).map((page) => page.site_name))), [pages.data]);
  return <>
    <section className="filter-panel"><div className="filter-grid"><label>분류<select value={filters.category} onChange={(event) => setFilters({ ...filters, category: event.target.value })}><option value="">전체</option><option value="government">정부·공공</option><option value="cafe">카페 이벤트</option></select></label><label>사이트<select value={filters.site} onChange={(event) => setFilters({ ...filters, site: event.target.value, page: "" })}><option value="">전체</option>{sites.map((site) => <option key={site}>{site}</option>)}</select></label><label>게시판<select value={filters.page} onChange={(event) => setFilters({ ...filters, page: event.target.value })}><option value="">전체</option>{(pages.data ?? []).filter((page) => !filters.site || page.site_name === filters.site).map((page) => <option value={page.id} key={page.id}>{page.page_name}</option>)}</select></label><label>전송 상태<select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">전체</option>{Object.entries(statusLabel).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="search-field">제목 검색<input placeholder="제목을 입력하세요" value={filters.title} onChange={(event) => setFilters({ ...filters, title: event.target.value })} /></label></div></section>
    <section className="section-heading"><div><p className="eyebrow">ARTICLES</p><h2>게시글 목록</h2></div><span className="result-count">최근 100건</span></section>
    {articles.isLoading ? <Loading /> : articles.isError ? <ErrorState /> : <ArticleTable articles={articles.data ?? []} />}
  </>;
}

function ArticleTable({ articles, compact = false }: { articles: Article[]; compact?: boolean }): JSX.Element {
  return <div className="panel table-panel"><table><thead><tr><th>분류</th><th>게시글</th><th>게시판</th><th>게시일</th><th>최초 발견</th><th>상태</th></tr></thead><tbody>{articles.map((article) => { const page = article.source_pages; const delivery = article.deliveries?.[0]; return <tr key={article.id}><td><span className={`category-pill ${page?.category ?? ""}`}>{page?.category === "cafe" ? "카페" : "공공"}</span></td><td className="title-cell"><a href={article.normalized_url} target="_blank" rel="noreferrer">{article.title}</a><small>{page?.site_name ?? "-"}</small></td><td>{page?.page_name ?? "-"}</td><td>{article.published_at ?? "-"}</td><td>{formatDate(article.first_detected_at)}</td><td>{delivery ? <span className={`status-pill ${delivery.status}`}>{statusLabel[delivery.status]}</span> : <span className="status-pill pending">대기</span>}</td></tr> })}{articles.length === 0 && <tr><td colSpan={6} className="empty-cell">표시할 게시글이 없습니다.</td></tr>}</tbody></table>{compact && articles.length > 0 && <div className="table-footnote">게시글 제목과 링크는 원본 목록 페이지에서만 수집합니다.</div>}</div>;
}

function PagesPage(): JSX.Element {
  const queryClient = useQueryClient();
  const pages = useQuery({ queryKey: ["pages"], queryFn: fetchSourcePages });
  const mutation = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateSourcePageEnabled(id, enabled), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pages"] }) });
  if (pages.isLoading) return <Loading />;
  if (pages.isError) return <ErrorState />;
  const rows = pages.data ?? [];
  return <div className="panel table-panel"><div className="table-intro"><div><p className="eyebrow">SOURCE PAGES</p><h2>게시판 관리</h2></div><span>{rows.length}개 등록</span></div><table><thead><tr><th>사이트 / 게시판</th><th>분류</th><th>Bot</th><th>초기화</th><th>최근 성공</th><th>최근 오류</th><th>상태</th></tr></thead><tbody>{rows.map((page: SourcePage) => <tr key={page.id}><td className="title-cell"><strong>{page.site_name}</strong><small>{page.page_name} · <a href={page.list_url} target="_blank" rel="noreferrer">원본 목록 ↗</a></small></td><td>{page.category === "cafe" ? "카페 이벤트" : "정부·공공"}</td><td><span className="bot-badge">{page.bot_profile}</span></td><td>{page.initialized ? <span className="ok-text">완료 · {page.baseline_article_count}건</span> : <span className="warn-text">미완료</span>}</td><td>{formatDate(page.last_success_at)}</td><td>{page.last_error_code ? <span className="error-text">{page.last_error_code}</span> : "-"}</td><td><label className="toggle"><input type="checkbox" checked={page.enabled} onChange={(event) => mutation.mutate({ id: page.id, enabled: event.target.checked })} /><span /></label></td></tr>)}</tbody></table></div>;
}

function DeliveriesPage(): JSX.Element {
  const queryClient = useQueryClient();
  const deliveries = useQuery({ queryKey: ["deliveries"], queryFn: fetchDeliveries });
  const mutation = useMutation({ mutationFn: requestDeliveryRetry, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["deliveries"] }) });
  if (deliveries.isLoading) return <Loading />;
  if (deliveries.isError) return <ErrorState />;
  const rows = deliveries.data ?? [];
  return <div className="panel table-panel"><div className="table-intro"><div><p className="eyebrow">DELIVERY LOG</p><h2>전송 이력</h2></div><span>최근 100건</span></div><table><thead><tr><th>게시글</th><th>Bot / 채널</th><th>상태</th><th>시도</th><th>Message ID</th><th>오류</th><th>작업</th></tr></thead><tbody>{rows.map((delivery) => <tr key={delivery.id}><td className="title-cell"><strong>{delivery.articles?.title ?? "-"}</strong><small>{delivery.articles?.source_pages?.site_name ?? "-"} · {delivery.articles?.source_pages?.page_name ?? "-"}</small></td><td><span className="bot-badge">{delivery.bot_profile}</span><small className="sub-cell">{delivery.target_chat_id}</small></td><td><span className={`status-pill ${delivery.status}`}>{statusLabel[delivery.status]}</span><small className="sub-cell">{formatDate(delivery.sent_at)}</small></td><td>{delivery.attempt_count}회</td><td>{delivery.telegram_message_id ?? "-"}</td><td className="error-cell">{delivery.last_error_message ?? "-"}</td><td>{["failed", "dead_letter"].includes(delivery.status) ? <button className="small-button" disabled={mutation.isPending} onClick={() => mutation.mutate(delivery.id)}>재전송 요청</button> : "-"}</td></tr>)}</tbody></table></div>;
}

function Loading(): JSX.Element { return <div className="panel loading">불러오는 중…</div>; }
function ErrorState(): JSX.Element { return <div className="panel error-state">데이터를 불러오지 못했습니다. Supabase 연결과 운영자 권한을 확인해 주세요.</div>; }

export default function App(): JSX.Element {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => { setUser(data.session?.user ?? null); setLoading(false); });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => setUser(session?.user ?? null));
    return () => listener.subscription.unsubscribe();
  }, []);
  if (loading) return <div className="loading-screen">연결 중…</div>;
  if (!user) return <Routes><Route path="*" element={<LoginPage />} /></Routes>;
  return <Layout user={user} />;
}
