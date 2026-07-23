import { apiClient } from '@/services/api';
import type {
  CustomerShipment,
  LogisticsSettings,
  LogisticsSettingsUpdate,
  ShipmentQueryResult,
  ShipmentTrackingEvent,
} from '@/types/logistics';

export const logisticsApi = {
  getSettings(): Promise<LogisticsSettings> {
    return apiClient.get<LogisticsSettings>('/v1/logistics/settings');
  },

  updateSettings(data: LogisticsSettingsUpdate): Promise<LogisticsSettings> {
    return apiClient.put<LogisticsSettings>('/v1/logistics/settings', data);
  },

  testTool(trackingNo: string): Promise<{
    success: boolean;
    message: string;
    preview: string | null;
  }> {
    return apiClient.post('/v1/logistics/settings/test', {
      tracking_no: trackingNo,
    });
  },

  async listShipments(visitorId: string): Promise<CustomerShipment[]> {
    const result = await apiClient.get<{ shipments: CustomerShipment[] }>(
      `/v1/logistics/visitors/${visitorId}/shipments`,
    );
    return result.shipments;
  },

  createShipment(
    visitorId: string,
    trackingNo: string,
    carrierName?: string,
  ): Promise<ShipmentQueryResult> {
    return apiClient.post<ShipmentQueryResult>(
      `/v1/logistics/visitors/${visitorId}/shipments`,
      {
        tracking_no: trackingNo,
        carrier_name: carrierName || null,
      },
    );
  },

  queryShipment(shipmentId: string): Promise<ShipmentQueryResult> {
    return apiClient.post<ShipmentQueryResult>(
      `/v1/logistics/shipments/${shipmentId}/query`,
    );
  },

  async listEvents(shipmentId: string): Promise<ShipmentTrackingEvent[]> {
    const result = await apiClient.get<{ events: ShipmentTrackingEvent[] }>(
      `/v1/logistics/shipments/${shipmentId}/events`,
    );
    return result.events;
  },
};
