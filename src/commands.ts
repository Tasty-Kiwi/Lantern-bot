export const commands = [
  {
    name: 'color', type: 1, description: 'Color replacer module of Lantern',
    integration_types: [0], contexts: [0],
    options: [
      {
        name: 'set', type: 1, description: 'Creates a custom role with a color code.',
        options: [{ name: 'color', type: 3, required: true, max_length: 64,
          description: 'Color hex code (e.g. ff0000) or name (e.g. hotpink)' }],
      },
      { name: 'clear', type: 1, description: 'Clears a color from your profile.' },
    ],
  },
  { name: 'about', type: 1, description: 'About Lantern', integration_types: [0], contexts: [0] },
];
