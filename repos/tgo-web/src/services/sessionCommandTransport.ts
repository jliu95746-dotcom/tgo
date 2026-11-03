import { visitorApiService } from './visitorApi';
import type { SessionCommandTransport } from '@/utils/sessionCommandRecovery';

/** Use the same saved identifier for the original POST and any explicit retry. */
export const sessionCommandTransport: SessionCommandTransport = {
  send: async command => {
    if (command.kind === 'close') {
      await visitorApiService.closeSession(command.visitorId, command.operationId);
    } else if (command.targetStaffId) {
      await visitorApiService.transferSession(
        command.visitorId, command.targetStaffId, undefined, command.operationId,
      );
    } else {
      throw new Error('转接目标缺失');
    }
  },
  lookup: operationId => visitorApiService.getSessionCommandReceipt(operationId),
};
