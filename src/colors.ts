import names from 'color-name';

export const COLOR_PATTERN = /^[0-9a-f]{6}$/i;

export function parseColor(input: string): string | null {
  const value = input.trim().toLowerCase();
  const hex = value.startsWith('#') ? value.slice(1) : value;
  let color: string;
  if (COLOR_PATTERN.test(hex)) {
    color = hex;
  } else {
    // webcolors used CSS3; rebeccapurple was introduced in CSS4.
    if (value === 'rebeccapurple' || !Object.hasOwn(names, value)) return null;
    const rgb = names[value as keyof typeof names];
    color = rgb.map((channel) => channel.toString(16).padStart(2, '0')).join('');
  }
  return color === '000000' ? '010101' : color;
}
