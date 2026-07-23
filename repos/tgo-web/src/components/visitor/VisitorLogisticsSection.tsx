import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  Loader2,
  PackagePlus,
  RefreshCw,
  Truck,
} from 'lucide-react';
import CollapsibleSection from '@/components/ui/CollapsibleSection';
import { logisticsApi } from '@/services/logisticsApi';
import type {
  CustomerShipment,
  ShipmentStatus,
  ShipmentTrackingEvent,
} from '@/types/logistics';

interface VisitorLogisticsSectionProps {
  visitorId: string;
  draggable?: boolean;
  className?: string;
  expanded?: boolean;
  onToggle?: (expanded: boolean) => void;
  onDragStart?: (event: React.DragEvent) => void;
  onDragEnd?: (event: React.DragEvent) => void;
}

const statusText: Record<ShipmentStatus, string> = {
  unknown: '待查询',
  pending: '待揽收',
  active: '已揽收',
  in_transit: '运输中',
  delivered: '已签收',
  exception: '物流异常',
};

const statusClass: Record<ShipmentStatus, string> = {
  unknown: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
  pending: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  active: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  in_transit: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  delivered: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  exception: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300',
};

const VisitorLogisticsSection: React.FC<VisitorLogisticsSectionProps> = ({
  visitorId,
  ...sectionProps
}) => {
  const [shipments, setShipments] = useState<CustomerShipment[]>([]);
  const [events, setEvents] = useState<Record<string, ShipmentTrackingEvent[]>>(
    {},
  );
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [trackingNo, setTrackingNo] = useState('');
  const [carrierName, setCarrierName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    () =>
      logisticsApi
        .listShipments(visitorId)
        .then(setShipments)
        .catch((loadError: unknown) =>
          setError(
            loadError instanceof Error ? loadError.message : '物流档案加载失败',
          ),
        )
        .finally(() => setLoading(false)),
    [visitorId],
  );

  useEffect(() => {
    setLoading(true);
    setError(null);
    void reload();
  }, [reload]);

  const handleAdd = async () => {
    if (!trackingNo.trim()) return;
    setBusyId('add');
    setError(null);
    try {
      await logisticsApi.createShipment(
        visitorId,
        trackingNo.trim(),
        carrierName.trim(),
      );
      setTrackingNo('');
      setCarrierName('');
      setShowAdd(false);
      await reload();
    } catch (addError) {
      setError(addError instanceof Error ? addError.message : '添加失败');
    } finally {
      setBusyId(null);
    }
  };

  const handleQuery = async (shipment: CustomerShipment) => {
    setBusyId(shipment.id);
    setError(null);
    try {
      const result = await logisticsApi.queryShipment(shipment.id);
      setShipments((current) =>
        current.map((item) =>
          item.id === result.shipment.id ? result.shipment : item,
        ),
      );
      setEvents((current) => ({
        ...current,
        [shipment.id]: result.events,
      }));
      setOpenId(shipment.id);
    } catch (queryError) {
      setError(queryError instanceof Error ? queryError.message : '查询失败');
    } finally {
      setBusyId(null);
    }
  };

  const toggleEvents = async (shipment: CustomerShipment) => {
    if (openId === shipment.id) {
      setOpenId(null);
      return;
    }
    setOpenId(shipment.id);
    if (!events[shipment.id]) {
      try {
        const loaded = await logisticsApi.listEvents(shipment.id);
        setEvents((current) => ({ ...current, [shipment.id]: loaded }));
      } catch (eventError) {
        setError(
          eventError instanceof Error ? eventError.message : '轨迹加载失败',
        );
      }
    }
  };

  return (
    <CollapsibleSection
      title="物流档案"
      {...sectionProps}
      rightContent={
        <span className="text-xs text-gray-400">{shipments.length} 单</span>
      }
    >
      <div className="space-y-3">
        {error && (
          <div className="flex items-start gap-2 rounded-md bg-red-50 dark:bg-red-900/20 p-2 text-xs text-red-600 dark:text-red-300">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-5 text-xs text-gray-400">
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            加载物流档案…
          </div>
        ) : shipments.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-200 dark:border-gray-700 py-5 text-center">
            <Truck className="w-6 h-6 mx-auto text-gray-300" />
            <p className="mt-2 text-xs text-gray-500">还没有关联物流单</p>
            <p className="mt-1 text-[11px] text-gray-400">
              收发含单号的消息后会自动出现
            </p>
          </div>
        ) : (
          shipments.map((shipment) => (
            <div
              key={shipment.id}
              className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden"
            >
              <div className="p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-800 dark:text-gray-200">
                        {shipment.tracking_no_masked}
                      </span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] ${statusClass[shipment.status]}`}
                      >
                        {statusText[shipment.status]}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-gray-500 truncate">
                      {shipment.carrier_name ||
                        shipment.latest_summary ||
                        '等待首次查询'}
                    </p>
                    {shipment.verification_state !== 'verified' && (
                      <p
                        className={`mt-1 text-[10px] ${
                          shipment.verification_state === 'conflict'
                            ? 'text-red-500'
                            : 'text-amber-600 dark:text-amber-400'
                        }`}
                      >
                        {shipment.verification_state === 'conflict'
                          ? '归属冲突 · 请人工核对'
                          : '顾客提供 · 待核验'}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleQuery(shipment)}
                    disabled={busyId === shipment.id}
                    className="p-1.5 rounded-md text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50"
                    aria-label="查询最新物流"
                  >
                    {busyId === shipment.id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => void toggleEvents(shipment)}
                  className="mt-2 w-full flex items-center justify-between text-[11px] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                >
                  <span className="flex items-center gap-1">
                    <Clock3 className="w-3 h-3" />
                    {shipment.last_checked_at
                      ? `更新于 ${new Date(shipment.last_checked_at).toLocaleString()}`
                      : '查看历史轨迹'}
                  </span>
                  {openId === shipment.id ? (
                    <ChevronUp className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5" />
                  )}
                </button>
              </div>
              {openId === shipment.id && (
                <div className="border-t border-gray-100 dark:border-gray-700 px-3 py-2 bg-gray-50/70 dark:bg-gray-900/30">
                  {(events[shipment.id] || []).length === 0 ? (
                    <p className="py-2 text-[11px] text-gray-400">
                      暂无轨迹，点击刷新查询最新状态。
                    </p>
                  ) : (
                    <div className="space-y-3 py-1">
                      {(events[shipment.id] || []).map((event, index) => (
                        <div key={event.id} className="flex gap-2">
                          <div className="pt-0.5">
                            {index === 0 ? (
                              <CheckCircle2 className="w-3.5 h-3.5 text-blue-500" />
                            ) : (
                              <span className="block w-2 h-2 mt-0.5 ml-0.5 rounded-full bg-gray-300" />
                            )}
                          </div>
                          <div>
                            <p className="text-[11px] leading-4 text-gray-700 dark:text-gray-300">
                              {event.description}
                            </p>
                            <p className="mt-0.5 text-[10px] text-gray-400">
                              {new Date(event.event_time).toLocaleString()}
                              {event.location ? ` · ${event.location}` : ''}
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}

        {showAdd ? (
          <div className="rounded-lg border border-blue-100 dark:border-blue-900 bg-blue-50/40 dark:bg-blue-900/10 p-3 space-y-2">
            <input
              value={trackingNo}
              onChange={(event) => setTrackingNo(event.target.value)}
              placeholder="物流单号"
              className="w-full h-8 px-2.5 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-xs"
            />
            <input
              value={carrierName}
              onChange={(event) => setCarrierName(event.target.value)}
              placeholder="快递公司（选填）"
              className="w-full h-8 px-2.5 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-xs"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowAdd(false)}
                className="px-2.5 py-1.5 text-xs text-gray-500"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleAdd}
                disabled={busyId === 'add' || !trackingNo.trim()}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-blue-600 text-xs text-white disabled:opacity-50"
              >
                {busyId === 'add' && (
                  <Loader2 className="w-3 h-3 animate-spin" />
                )}
                添加
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="w-full inline-flex items-center justify-center gap-1.5 py-2 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 text-xs text-gray-500 hover:border-blue-300 hover:text-blue-600"
          >
            <PackagePlus className="w-3.5 h-3.5" />
            手动添加物流单
          </button>
        )}
      </div>
    </CollapsibleSection>
  );
};

export default VisitorLogisticsSection;
