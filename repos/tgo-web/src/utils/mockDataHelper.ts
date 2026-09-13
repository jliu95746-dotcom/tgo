/**
 * Mock Data Helper - Conditionally loads mock data only in development
 * Prevents mock data from being included in production builds
 */

import type { Agent, Platform, Chat, Message } from '@/types';

const IS_DEVELOPMENT = import.meta.env.DEV;

/**
 * Dynamically import mock data only in development environment
 * Returns empty arrays in production to prevent mock data leakage
 */
export const mockDataHelper = {
  /**
   * Get mock agents - only in development
   */
  async getAgents(): Promise<Agent[]> {
    if (!IS_DEVELOPMENT) return [];
    
    try {
      const { mockAgents } = await import('@/data/mockAgents');
      return mockAgents;
    } catch {
      console.warn('Failed to load mock agents');
      return [];
    }
  },

  /**
   * Get mock platforms - only in development
   */
  async getPlatforms(): Promise<Platform[]> {
    if (!IS_DEVELOPMENT) return [];

    try {
      const { mockPlatforms } = await import('@/data/mockPlatforms');
      return mockPlatforms;
    } catch {
      console.warn('Failed to load mock platforms');
      return [];
    }
  },

  /**
   * Get mock chats - disabled (mock chats removed)
   */
  async getChats(): Promise<Chat[]> {
    return [];
  },

  /**
   * Get mock messages - only in development
   */
  async getMessages(): Promise<Message[]> {
    if (!IS_DEVELOPMENT) return [];
    
    try {
      const { mockMessages } = await import('@/data/mockMessages');
      return mockMessages;
    } catch {
      console.warn('Failed to load mock messages');
      return [];
    }
  },

  /**
   * Get mock visitor data - only in development
   */
  async getVisitor(): Promise<any> {
    if (!IS_DEVELOPMENT) return null;
    
    try {
      const { mockVisitor } = await import('@/data/mockVisitor');
      return mockVisitor;
    } catch {
      console.warn('Failed to load mock visitor');
      return null;
    }
  },

  /**
   * Check if we're in development mode
   */
  isDevelopment(): boolean {
    return IS_DEVELOPMENT;
  }
};

export default mockDataHelper;
