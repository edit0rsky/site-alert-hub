export type DeliveryStatus =
  | "pending"
  | "sending"
  | "sent"
  | "failed"
  | "retry_requested"
  | "dead_letter"
  | "skipped_initial_sync";

export interface SourcePage {
  id: string;
  site_name: string;
  page_name: string;
  category: string;
  list_url: string;
  bot_profile: string;
  enabled: boolean;
  initialized: boolean;
  initialized_at: string | null;
  baseline_article_count: number;
  last_crawled_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  consecutive_failure_count: number;
}

export interface Delivery {
  id: string;
  bot_profile: string;
  target_chat_id: string;
  status: DeliveryStatus;
  attempt_count: number;
  telegram_message_id: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  next_retry_at: string | null;
  sent_at: string | null;
  delivery_generation: number;
  article_id: string;
  articles?: Article;
}

export interface Article {
  id: string;
  source_page_id: string;
  title: string;
  original_url: string;
  normalized_url: string;
  published_at: string | null;
  first_detected_at: string;
  source_pages?: SourcePage;
  deliveries?: Delivery[];
}

export interface DashboardSummary {
  newArticles: number;
  sent: number;
  failed: number;
  retry: number;
  activePages: number;
  errorPages: number;
}

export interface ArticleFilters {
  category: string;
  site: string;
  page: string;
  status: string;
  title: string;
}

