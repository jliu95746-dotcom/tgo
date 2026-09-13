/**
 * Tag Color Utilities
 * 
 * Provides shared logic for handling tag colors, including hex normalization
 * and RGBA conversion for background opacity.
 */

const TAG_COLOR_NAME_TO_HEX: Record<string, string> = {
  red: '#ef4444',
  rose: '#f43f5e',
  pink: '#ec4899',
  purple: '#a855f7',
  indigo: '#6366f1',
  blue: '#3b82f6',
  sky: '#0ea5e9',
  teal: '#14b8a6',
  green: '#22c55e',
  lime: '#84cc16',
  yellow: '#eab308',
  amber: '#f59e0b',
  orange: '#f97316',
  gray: '#6b7280',
  slate: '#64748b',
  emerald: '#10b981',
};

/**
 * Normalizes a color string (name or hex) to a 6-digit hex code with # prefix.
 */
export const normalizeTagHex = (color?: string | null): string => {
  if (!color) return TAG_COLOR_NAME_TO_HEX.blue;
  
  const trimmed = color.trim().toLowerCase();
  
  // If it's already a valid hex
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(trimmed)) {
    if (trimmed.length === 4) {
      // #ABC -> #AABBCC
      return `#${  trimmed[1]  }${trimmed[1]  }${trimmed[2]  }${trimmed[2]  }${trimmed[3]  }${trimmed[3]}`;
    }
    return trimmed;
  }
  
  // If it's a known color name
  if (TAG_COLOR_NAME_TO_HEX[trimmed]) {
    return TAG_COLOR_NAME_TO_HEX[trimmed];
  }
  
  // If it's a hex without #
  if (/^([0-9a-f]{3}|[0-9a-f]{6})$/i.test(trimmed)) {
    if (trimmed.length === 3) {
      return `#${  trimmed[0]  }${trimmed[0]  }${trimmed[1]  }${trimmed[1]  }${trimmed[2]  }${trimmed[2]}`;
    }
    return `#${  trimmed}`;
  }
  
  return TAG_COLOR_NAME_TO_HEX.blue;
};

/**
 * Converts a hex color string to rgba format with specified alpha.
 */
export const hexToRgba = (hex: string, alpha: number): string => {
  const normalized = normalizeTagHex(hex).replace('#', '');
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};
