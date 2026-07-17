import type {
  CombinedMessageAnalysis,
  MessageAnalysisViewState,
  MessageIntentResult,
  RoutingReason,
} from '../messageAnalysis';

const routingReason: RoutingReason = 'untrusted_media_confirmation';

const intentResult = {
  id: 'intent-result-id',
  project_id: 'project-id',
  platform_id: 'platform-id',
  source_message_id: 'source-message-id',
  visitor_id: 'visitor-id',
  media_analysis_result_id: null,
  intent: 'logistics_query',
  confidence: 0.93,
  entities: {
    order_no: 'ORDER-001',
    product_name: null,
    sku: null,
    logistics_no: null,
    payment_reference: null,
    issue_summary: null,
  },
  risk_level: 'low',
  recommended_route: 'read_only_tool',
  need_human: false,
  taxonomy_version: 'v1',
  routing_reason: routingReason,
  classification_source: 'model',
  classifier_version: 'intent-v1',
  policy_version: 'policy-v1',
  request_id: null,
  input_fingerprint: 'fingerprint',
  created_at: '2026-07-16T00:00:00Z',
  updated_at: '2026-07-16T00:00:00Z',
} satisfies MessageIntentResult;

const analysis = {
  source_message_id: 'source-message-id',
  media: null,
  intent: intentResult,
} satisfies CombinedMessageAnalysis;

const availableState: MessageAnalysisViewState = {
  status: 'available',
  analysis,
};

// Invalid write-capable routes must remain outside the console contract.
// @ts-expect-error Only policy-approved routes may be displayed.
const unsafeRoute: MessageIntentResult['recommended_route'] = 'refund_order';

void availableState;
void unsafeRoute;
