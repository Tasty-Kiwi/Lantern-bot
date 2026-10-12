import { describe, expect, it } from 'vitest';
import { parseColor } from '../src/colors.ts';
import { commands } from '../src/commands.ts';

describe('legacy color inputs', () => {
  it.each([
    ['ff0011', 'ff0011'], [' #ABCDEF ', 'abcdef'], [' HOTPINK ', 'ff69b4'],
    ['darkslategrey', '2f4f4f'], ['gray', '808080'], ['grey', '808080'],
    ['black', '010101'], ['000000', '010101'], ['#000000', '010101'],
  ])('normalizes %s', (input, expected) => expect(parseColor(input)).toBe(expected));

  it.each(['', '#fff', 'ff00000', 'transparent', 'rebeccapurple', 'rgb(0,0,0)', '#hotpink', 'constructor', '__proto__'])('rejects %s', (input) => {
    expect(parseColor(input)).toBeNull();
  });

  it('registers only color set/clear and about', () => {
    expect(commands.map((command) => command.name)).toEqual(['color', 'about']);
    expect(commands[0]?.options?.map((sub) => sub.name)).toEqual(['set', 'clear']);
    expect(commands.every((command) => command.contexts.length === 1 && command.contexts[0] === 0)).toBe(true);
    expect(commands.every((command) => command.integration_types.length === 1 && command.integration_types[0] === 0)).toBe(true);
  });
});
