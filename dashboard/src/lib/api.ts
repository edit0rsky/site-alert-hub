import { supabase } from "./supabase";
import type { Article, ArticleFilters, DashboardSummary, Delivery, SourcePage } from "../types";

const throwIfError = (error: Error | null): void => {
  if (error) throw error;
};

export async function fetchSourcePages(): Promise<SourcePage[]> {
  const { data, error } = await supabase.from("source_pages").select("*").order("site_name");
  throwIfError(error);
  return (data ?? []) as SourcePage[];
}

export async function updateSourcePageEnabled(id: string, enabled: boolean): Promise<void> {
  const { error } = await supabase.from("source_pages").update({ enabled }).eq("id", id);
  throwIfError(error);
}

export async function fetchArticles(filters: ArticleFilters): Promise<Article[]> {
  let query = supabase
    .from("articles")
    .select("*, source_pages(*), deliveries(*)")
    .order("first_detected_at", { ascending: false })
    .limit(100);
  if (filters.page) query = query.eq("source_page_id", filters.page);
  if (filters.title) query = query.ilike("title", `%${filters.title}%`);
  const { data, error } = await query;
  throwIfError(error);
  return (data ?? []).filter((article) => {
    const page = article.source_pages as SourcePage | null;
    const delivery = (article.deliveries as Delivery[] | null)?.[0];
    return (!filters.category || page?.category === filters.category)
      && (!filters.site || page?.site_name === filters.site)
      && (!filters.status || delivery?.status === filters.status);
  }) as Article[];
}

export async function fetchDeliveries(): Promise<Delivery[]> {
  const { data, error } = await supabase
    .from("deliveries")
    .select("*, articles(*, source_pages(*))")
    .order("updated_at", { ascending: false })
    .limit(100);
  throwIfError(error);
  return (data ?? []) as Delivery[];
}

export async function requestDeliveryRetry(deliveryId: string): Promise<void> {
  const { error } = await supabase.rpc("request_delivery_retry", { p_delivery_id: deliveryId });
  throwIfError(error);
}

export async function fetchSummary(): Promise<DashboardSummary> {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayIso = today.toISOString();
  const [pagesResult, articlesResult, deliveriesResult] = await Promise.all([
    supabase.from("source_pages").select("enabled, consecutive_failure_count"),
    supabase.from("articles").select("id").gte("first_detected_at", todayIso),
    supabase.from("deliveries").select("status").gte("updated_at", todayIso),
  ]);
  throwIfError(pagesResult.error);
  throwIfError(articlesResult.error);
  throwIfError(deliveriesResult.error);
  const deliveries = deliveriesResult.data ?? [];
  const count = (status: string): number => deliveries.filter((item) => item.status === status).length;
  return {
    newArticles: articlesResult.data?.length ?? 0,
    sent: count("sent"),
    failed: count("failed") + count("dead_letter"),
    retry: count("pending") + count("retry_requested"),
    activePages: (pagesResult.data ?? []).filter((page) => page.enabled).length,
    errorPages: (pagesResult.data ?? []).filter((page) => page.consecutive_failure_count > 0).length,
  };
}
