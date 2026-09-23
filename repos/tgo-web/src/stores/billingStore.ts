import { create } from 'zustand';
import { billingApi } from '../services/billingApi';
import type { BillingOrder, BillingPlan, BillingQuote, BillingSubscription, QuoteRequest } from '../types/billing';

interface BillingState {
  subscription: BillingSubscription | null; plans: BillingPlan[]; orders: BillingOrder[];
  quote: BillingQuote | null; order: BillingOrder | null; busy: boolean; error: string | null;
  serverClockOffset: number;
  load: (admin: boolean, offset?: number) => Promise<void>;
  getQuote: (request: QuoteRequest) => Promise<void>;
  pay: () => Promise<void>; viewOrder: (id: string) => Promise<void>;
  pollOrder: () => Promise<void>; close: () => void; reset: () => void;
}
const initial = { subscription: null, plans: [], orders: [], quote: null, order: null, busy: false, error: null, serverClockOffset: 0 };
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'billing.error';
let generation = 0;
let polling = false;
let loadRevision = 0;
const clockOffset = (subscription: BillingSubscription) => {
  const serverTime = Date.parse(subscription.server_time);
  return Number.isFinite(serverTime) ? serverTime - Date.now() : 0;
};

export const useBillingStore = create<BillingState>((set, get) => ({
  ...initial,
  async load(admin, offset = 0) {
    const currentGeneration = generation;
    const currentLoad = ++loadRevision;
    set({ busy: true, error: null });
    try {
      const [subscription, plans, orders] = await Promise.all([
        billingApi.subscription(), billingApi.plans(), admin ? billingApi.orders(offset) : Promise.resolve([]),
      ]);
      if (currentGeneration === generation && currentLoad === loadRevision) set({ subscription, plans, orders, serverClockOffset: clockOffset(subscription) });
    } catch (error) { if (currentGeneration === generation && currentLoad === loadRevision) set({ error: errorMessage(error) }); }
    finally { if (currentGeneration === generation && currentLoad === loadRevision) set({ busy: false }); }
  },
  async getQuote(request) {
    const currentGeneration = ++generation;
    set({ busy: true, error: null, quote: null, order: null });
    try { const quote = await billingApi.quote(request); if (currentGeneration === generation) set({ quote }); }
    catch (error) { if (currentGeneration === generation) set({ error: errorMessage(error) }); }
    finally { if (currentGeneration === generation) set({ busy: false }); }
  },
  async pay() {
    const quote = get().quote;
    if (!quote || get().busy) return;
    const currentGeneration = generation;
    set({ busy: true, error: null });
    try {
      const order = await billingApi.createOrder(quote.id);
      if (currentGeneration !== generation) return;
      set({ order, quote: null });
      if (order.payment_status !== 'paid') {
        const checkout = await billingApi.checkout(order.id);
        if (currentGeneration === generation) set({ order: checkout });
      }
    } catch (error) { if (currentGeneration === generation) set({ error: errorMessage(error) }); }
    finally { if (currentGeneration === generation) set({ busy: false }); }
  },
  async viewOrder(id) {
    const currentGeneration = ++generation;
    set({ busy: true, error: null, quote: null });
    try {
      const order = await billingApi.order(id);
      if (currentGeneration !== generation) return;
      set({ order });
      if (order.payment_status === 'pending' && !order.code_url && new Date(order.expires_at).getTime() > Date.now() + get().serverClockOffset) {
        const checkout = await billingApi.checkout(id);
        if (currentGeneration === generation) set({ order: checkout });
      }
    } catch (error) { if (currentGeneration === generation) set({ error: errorMessage(error) }); }
    finally { if (currentGeneration === generation) set({ busy: false }); }
  },
  async pollOrder() {
    const current = get().order;
    if (!current || polling) return;
    const currentGeneration = generation;
    polling = true;
    try {
      const order = await billingApi.order(current.id);
      if (get().order?.id !== current.id || currentGeneration !== generation) return;
      set({ order });
      if (order.fulfillment_status === 'applied' && current.fulfillment_status !== 'applied') {
        const subscription = await billingApi.subscription();
        if (currentGeneration === generation) set({ subscription, serverClockOffset: clockOffset(subscription) });
      }
    } catch (error) { if (currentGeneration === generation) set({ error: errorMessage(error) }); }
    finally { polling = false; }
  },
  close: () => { generation++; set({ quote: null, order: null, error: null, busy: false }); },
  reset: () => { generation++; set(initial); },
}));
