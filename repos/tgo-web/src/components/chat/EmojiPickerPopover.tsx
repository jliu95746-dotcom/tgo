import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export interface EmojiPickerPopoverProps {
  anchorRef: React.RefObject<HTMLElement | null> | React.RefObject<HTMLButtonElement | null>;
  onSelect: (emoji: string) => void;
  onClose: () => void;
  /** Optional fixed width/height for the picker panel */
  width?: number;
  height?: number;
}

const DEFAULT_WIDTH = 360;
const DEFAULT_HEIGHT = 420;
const MARGIN = 8; // distance from anchor



type CategoryKey = 'smileys' | 'animals' | 'food' | 'activities' | 'travel' | 'objects' | 'symbols' | 'flags';

const CATEGORY_LIST: { key: CategoryKey; label: string; icon: string }[] = [
  { key: 'smileys', label: '表情', icon: '😊' },
  { key: 'animals', label: '动物', icon: '🐻' },
  { key: 'food', label: '美食', icon: '🍔' },
  { key: 'activities', label: '活动', icon: '⚽️' },
  { key: 'travel', label: '旅行', icon: '🚗' },
  { key: 'objects', label: '物品', icon: '🔧' },
  { key: 'symbols', label: '符号', icon: '🔣' },


  { key: 'flags', label: '旗帜', icon: '🏳️' },
];

const EMOJI_DATA: Record<CategoryKey, string> = {
  smileys: '😀 😃 😄 😁 😆 😅 😂 🙂 😉 😊 😇 😍 😘 😗 😙 😚 😋 😛 😜 🤪 🤨 🫤 😐 😑 😶 🤗 🤭 🤫 🤔 🙄 😏 😣 😥 😮‍💨 😮 😯 😲 🥱 😴 🤤 😪 🥵 🥶 😷 🤕 🤒 🤧 🥳 😎 😭 😤 😡 😠 😳 😱',
  animals: '🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🐔 🐧 🐦 🐤 🐣 🐺 🐗 🐴 🦄 🐝 🐛 🦋 🐌 🐞 🐢 🐍 🦖 🦕 🐙 🦑 🦐 🦞 🐠 🐟 🐡 🐬 🐳 🐋',
  food: '🍏 🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍈 🍒 🍑 🍍 🥭 🍅 🍆 🥑 🥦 🥬 🥒 🌽 🥕 🧄 🧅 🥔 🍞 🥐 🥖 🥨 🥯 🧇 🥞 🧈 🧀 🍗 🍖 🍔 🍟 🍕 🌭 🥪 🌮 🌯 🥙 🧆 🍜 🍝 🍣',
  activities: '⚽️ 🏀 🏈 ⚾️ 🎾 🏐 🏉 🎱 🏓 🏸 🥅 🏒 🏑 🥍 🏏 🛼 ⛸️ 🛹 🛷 ⛷️ 🏂 🧗‍♀️ 🧗‍♂️ 🚴‍♀️ 🚴‍♂️ 🏊‍♀️ 🏊‍♂️ 🧘‍♀️ 🧘‍♂️ 🥇 🥈 🥉 🏆 🎯 🎮 🎲 🧩 🎻 🎸 🎹 🎺 🥁',
  travel: '🚗 🚕 🚙 🚌 🚎 🏎️ 🚓 🚑 🚒 🚐 🚚 🚛 🚜 🛴 🚲 🏍️ 🛵 ✈️ 🛫 🛬 🛩️ 🚁 🚂 🚆 🚄 🚅 🚈 🚊 🚉 ⛵️ 🚤 🛳️ ⛴️ 🚀 🛸 🏠 🏥 🏫 🏢 🏬 🏭 🏰 🗼 🗽',
  objects: '⌚️ 📱 💻 ⌨️ 🖥️ 🖨️ 🖱️ 🖲️ 💽 💾 💿 📷 📸 📹 🎥 📞 ☎️ 📟 📠 🔋 🔌 💡 🔦 🕯️ 🧯 🧷 🧹 🧺 🧻 🧼 🧽 🧴 🪒 🔧 🔨 ⚙️ 🧲 🔩 🔗 🧰 🪑 🛏️ 🛋️',
  symbols: '❤️ 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❣️ 💕 💞 💓 💗 💖 💘 💝 🔥 💧 ✨ ⭐️ 🌟 💫 ⚡️ 💥 💦 💨 💢 💬 💭 ♻️ ✅ ❌ ❓ ❗️ 🔞 🚫 ⛔️ ⚠️ ♾️ ➕ ➖ ➗ ✖️',
  flags: '🏳️ 🏴 🏁 🚩 🏳️‍🌈 🏳️‍⚧️ 🇨🇳 🇺🇸 🇯🇵 🇰🇷 🇫🇷 🇩🇪 🇬🇧 🇮🇳 🇨🇦 🇦🇺 🇧🇷 🇪🇸 🇮🇹 🇷🇺 🇲🇽 🇸🇬 🇭🇰 🇹🇼',
};


const EmojiPickerPopover: React.FC<EmojiPickerPopoverProps> = ({
  anchorRef,
  onSelect,
  onClose,
  width = DEFAULT_WIDTH,
  height = DEFAULT_HEIGHT,
}) => {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ top: 0, left: 0, width, height });

  const [activeCat, setActiveCat] = useState<CategoryKey>('smileys');

  const computePosition = useCallback(() => {
    const anchor = anchorRef.current as HTMLElement | null;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const availableHeight = window.visualViewport?.height ?? window.innerHeight;
    const panelWidth = Math.min(width, window.innerWidth - 16);
    const panelHeight = Math.min(height, availableHeight - 16);

    // Prefer showing above; flip to below if not enough space
    const hasSpaceAbove = rect.top >= panelHeight + MARGIN + 8;
    const hasSpaceBelow = availableHeight - rect.bottom >= panelHeight + MARGIN + 8;

    let nextPlacement: 'top' | 'bottom' = hasSpaceAbove ? 'top' : 'bottom';
    if (!hasSpaceAbove && !hasSpaceBelow) {
      // Pick the side with more space
      nextPlacement = rect.top > window.innerHeight - rect.bottom ? 'top' : 'bottom';
    }

    let top: number;
    if (nextPlacement === 'top') {
      top = Math.max(8, rect.top - panelHeight - MARGIN);
    } else {
      top = Math.max(8, Math.min(availableHeight - panelHeight - 8, rect.bottom + MARGIN));
    }

    const centerLeft = rect.left + rect.width / 2 - panelWidth / 2;
    const left = Math.max(8, Math.min(centerLeft, window.innerWidth - panelWidth - 8));

    setPos({ top, left, width: panelWidth, height: panelHeight });
  }, [anchorRef, height, width]);

  useLayoutEffect(() => {
    computePosition();
  }, [computePosition]);

  useEffect(() => {
    const handler = () => computePosition();
    window.addEventListener('resize', handler);
    window.addEventListener('scroll', handler, true);
    window.visualViewport?.addEventListener('resize', handler);
    return () => {
      window.removeEventListener('resize', handler);
      window.removeEventListener('scroll', handler, true);
      window.visualViewport?.removeEventListener('resize', handler);
    };
  }, [computePosition]);

  const panel = (
    <div
      ref={panelRef}
      className="fixed z-[1000] bg-white dark:bg-gray-900 rounded-md shadow-lg border border-gray-200 dark:border-gray-700"
      style={{ top: pos.top, left: pos.left, width: pos.width, height: pos.height }}
      role="dialog"
      aria-label="Emoji picker"
    >
      <div className="flex h-full w-full flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <div className="grid grid-cols-[repeat(auto-fit,minmax(2rem,1fr))] gap-1 text-2xl">
            {EMOJI_DATA[activeCat].split(' ').filter(Boolean).map((emoji) => (
              <button
                key={emoji}
                className="h-9 min-w-0 flex items-center justify-center rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                onClick={() => onSelect(emoji)}
                aria-label={`插入 ${emoji}`}
              >
                <span>{emoji}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="shrink-0 overflow-x-auto border-t border-gray-200 dark:border-gray-700 p-2 flex items-center justify-between" role="tablist">
          {CATEGORY_LIST.map((cat) => (
            <button
              key={cat.key}
              role="tab"
              aria-selected={activeCat === cat.key}
              title={cat.label}
              onClick={() => setActiveCat(cat.key)}
              className={`shrink-0 text-xl p-1.5 rounded transition-colors ${activeCat === cat.key ? 'bg-gray-100 dark:bg-gray-800 text-blue-600 dark:text-blue-400' : 'text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800'}`}
            >
              <span>{cat.icon}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );

  const overlay = (
    <div
      className="fixed inset-0 z-[999]"
      onClick={onClose}
      aria-hidden="true"
    />
  );

  // Render to body via portals
  return (
    <>
      {createPortal(overlay, document.body)}
      {createPortal(panel, document.body)}
    </>
  );
};

export default EmojiPickerPopover;

