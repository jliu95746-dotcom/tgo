import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
import { createPortal } from 'react-dom';

export type ChatTabType = 'mine' | 'unassigned' | 'recent' | 'all' | 'manual';

interface ChatListTabsProps {
  activeTab: ChatTabType;
  onTabChange: (tab: ChatTabType) => void;
  counts: {
    mine: number;      // "我的" tab 显示未读数量
    unassigned: number; // "未分配" tab 显示等待数量
    // "全部" tab 不显示数量
  };
}

export const ChatListTabs: React.FC<ChatListTabsProps> = ({ activeTab, onTabChange, counts }) => {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const [indicatorStyle, setIndicatorStyle] = useState({ left: 0, width: 0, opacity: 1 });
  const [visibleTabCount, setVisibleTabCount] = useState<number>(4); // 初始全部显示
  const [isMoreOpen, setIsMoreOpen] = useState(false);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, right: 0 });

  const workflowTabs = useMemo(() => [
    { key: 'mine' as ChatTabType, label: t('chat.list.tabs.mine', '我的'), count: counts.mine > 0 ? counts.mine : undefined },
    { key: 'unassigned' as ChatTabType, label: t('chat.list.tabs.unassigned', '未分配'), count: counts.unassigned > 0 ? counts.unassigned : undefined },
    { key: 'all' as ChatTabType, label: t('chat.list.tabs.all', '已完成') },
    { key: 'manual' as ChatTabType, label: t('chat.list.tabs.manual', '转人工') },
  ], [t, counts.mine, counts.unassigned]);

  // 计算哪些 Tab 需要收起
  const updateTabVisibility = useCallback(() => {
    if (!containerRef.current) return;
    
    const container = containerRef.current;
    const containerWidth = container.offsetWidth;
    const padding = 32; // 左右 padding 合计
    const gap = 20; // tabs 之间的 gap
    const moreBtnWidth = 40; // “更多”按钮的预估宽度
    const availableWidth = containerWidth - padding;

    // 临时创建一个测量用的 div 来获取各 tab 的宽度
    const buttons = Array.from(container.querySelectorAll('button[data-measure-tab]')) as HTMLElement[];
    if (buttons.length === 0) return;

    let currentTotalWidth = 0;
    let newVisibleCount = workflowTabs.length;

    for (let i = 0; i < buttons.length; i++) {
      const btn = buttons[i];
      const btnWidth = btn.offsetWidth;
      const spacing = i === 0 ? 0 : gap;
      
      // 如果加上当前按钮超过了可用宽度
      if (currentTotalWidth + spacing + btnWidth > availableWidth) {
        // 如果不是第一个，且还没扣除更多按钮宽度，则尝试重新计算
        if (i > 0 && currentTotalWidth + spacing + moreBtnWidth > availableWidth) {
           newVisibleCount = i - 1;
        } else {
           newVisibleCount = i;
        }
        break;
      }
      currentTotalWidth += spacing + btnWidth;
    }

    setVisibleTabCount(Math.max(1, newVisibleCount)); // 至少保留一个
  }, [workflowTabs.length]);

  const updateIndicator = useCallback(() => {
    if (!containerRef.current) return;

    if (activeTab === 'recent') {
      setIndicatorStyle(prev => ({ ...prev, opacity: 0 }));
      return;
    }
    
    const activeButton = containerRef.current.querySelector(`[data-tab="${activeTab}"]`) as HTMLElement;
    if (activeButton) {
      setIndicatorStyle({
        left: activeButton.offsetLeft,
        width: activeButton.offsetWidth,
        opacity: 1
      });
    } else {
      // 如果活跃 tab 在“更多”里面，指示器移动到“更多”按钮
      if (moreButtonRef.current) {
        setIndicatorStyle({
          left: moreButtonRef.current.offsetLeft,
          width: moreButtonRef.current.offsetWidth,
          opacity: 1
        });
      } else {
        setIndicatorStyle(prev => ({ ...prev, opacity: 0 }));
      }
    }
  }, [activeTab]);

  useEffect(() => {
    updateTabVisibility();
    const observer = new ResizeObserver(() => {
      updateTabVisibility();
    });
    if (containerRef.current) {
      observer.observe(containerRef.current);
    }
    return () => observer.disconnect();
  }, [updateTabVisibility]);

  useEffect(() => {
    updateIndicator();
  }, [updateIndicator, visibleTabCount]);

  const visibleTabs = workflowTabs.slice(0, visibleTabCount);
  const hiddenTabs = workflowTabs.slice(visibleTabCount);
  const isActiveInMore = hiddenTabs.some(t => t.key === activeTab);

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, currentTab: ChatTabType) => {
    const currentIndex = workflowTabs.findIndex(tab => tab.key === currentTab);
    if (currentIndex < 0) return;

    let nextIndex = currentIndex;
    if (event.key === 'ArrowRight') {
      nextIndex = (currentIndex + 1) % workflowTabs.length;
    } else if (event.key === 'ArrowLeft') {
      nextIndex = (currentIndex - 1 + workflowTabs.length) % workflowTabs.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = workflowTabs.length - 1;
    } else {
      return;
    }

    event.preventDefault();
    onTabChange(workflowTabs[nextIndex].key);
  };

  const toggleMore = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!moreButtonRef.current) return;
    const rect = moreButtonRef.current.getBoundingClientRect();
    setDropdownPos({
      top: rect.bottom + window.scrollY,
      right: window.innerWidth - rect.right
    });
    setIsMoreOpen(!isMoreOpen);
  };

  useEffect(() => {
    const handleClickOutside = () => setIsMoreOpen(false);
    if (isMoreOpen) {
      window.addEventListener('click', handleClickOutside);
    }
    return () => window.removeEventListener('click', handleClickOutside);
  }, [isMoreOpen]);

  return (
    <div 
      ref={containerRef}
      role="tablist"
      aria-label={t('chat.list.tabs.ariaLabel', '会话状态')}
      className="relative flex items-stretch px-4 gap-5 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 overflow-hidden"
    >
      {/* 用于测量的隐藏层，始终渲染所有 tabs 以获取宽度 */}
      <div className="absolute top-0 left-0 invisible pointer-events-none flex gap-5 px-4 h-0 overflow-hidden">
        {workflowTabs.map(tab => (
          <button
            key={`measure-${tab.key}`}
            data-measure-tab={tab.key}
            aria-hidden="true"
            tabIndex={-1}
            className="py-2.5 text-[13px] font-medium flex items-center gap-1.5 whitespace-nowrap"
          >
            <span>{tab.label}</span>
            {tab.count !== undefined && <span className="text-[11px] tabular-nums">{tab.count}</span>}
          </button>
        ))}
      </div>

      {visibleTabs.map((tab) => {
        const isActive = activeTab === tab.key;
        return (
          <button
            key={tab.key}
            data-tab={tab.key}
            id={`chat-tab-${tab.key}`}
            role="tab"
            aria-selected={isActive}
            aria-controls="chat-list-panel"
            tabIndex={isActive ? 0 : -1}
            onClick={() => onTabChange(tab.key)}
            onKeyDown={(event) => handleTabKeyDown(event, tab.key)}
            className={`
              relative py-2.5 text-[13px] font-medium transition-all duration-200 outline-none
              focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2
              flex items-center gap-1.5 whitespace-nowrap
              ${isActive 
                ? 'text-gray-900 dark:text-white' 
                : 'text-gray-600 dark:text-gray-300 hover:text-gray-800 dark:hover:text-white'
              }
            `}
          >
            <span>{tab.label}</span>
            {tab.count !== undefined && (
              <span className="text-[11px] tabular-nums text-gray-400 dark:text-gray-500">{tab.count}</span>
            )}
          </button>
        );
      })}

      {hiddenTabs.length > 0 && (
        <button
          ref={moreButtonRef}
          onClick={toggleMore}
          aria-haspopup="menu"
          aria-expanded={isMoreOpen}
          aria-label={t('chat.list.tabs.moreAria', '更多会话状态')}
          className={`
            relative py-2.5 text-[13px] font-medium transition-all duration-200 outline-none
            focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2
            flex items-center gap-1 min-w-[40px] justify-center
            ${isActiveInMore || isMoreOpen
              ? 'text-gray-900 dark:text-white' 
              : 'text-gray-600 dark:text-gray-300 hover:text-gray-800 dark:hover:text-white'
            }
          `}
        >
          <span>{t('common.more', '更多')}</span>
          <ChevronDown size={14} className={`transition-transform duration-200 ${isMoreOpen ? 'rotate-180' : ''}`} />
        </button>
      )}
      
      {/* Animated indicator line */}
      <div
        className="absolute bottom-0 h-0.5 bg-blue-500 dark:bg-blue-400 rounded-full transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]"
        style={{
          left: indicatorStyle.left,
          width: indicatorStyle.width,
          opacity: indicatorStyle.opacity,
          transform: 'translateZ(0)',
        }}
      />

      {/* Dropdown Menu */}
      {isMoreOpen && createPortal(
        <div 
          role="menu"
          className="fixed z-[1000] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl py-1 min-w-[120px] animate-in fade-in zoom-in-95 duration-100"
          style={{ 
            top: dropdownPos.top + 4,
            right: dropdownPos.right
          }}
          onClick={e => e.stopPropagation()}
        >
          {hiddenTabs.map(tab => (
            <button
              key={tab.key}
              role="menuitemradio"
              aria-checked={activeTab === tab.key}
              onClick={() => {
                onTabChange(tab.key);
                setIsMoreOpen(false);
              }}
              className={`
                w-full px-4 py-2 text-left text-[13px] flex items-center justify-between
                transition-colors duration-150 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500
                ${activeTab === tab.key 
                  ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400' 
                  : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
                }
              `}
            >
              <span>{tab.label}</span>
              {tab.count !== undefined && (
                <span className="text-[11px] tabular-nums bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded-full min-w-[18px] text-center ml-2">
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>,
        document.body
      )}
    </div>
  );
};
