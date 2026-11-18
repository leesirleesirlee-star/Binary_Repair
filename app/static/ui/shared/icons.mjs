const paths = {
  mark: '<path d="M12 2.8 20 6v5.6c0 4.5-3.2 7.2-8 9.6-4.8-2.4-8-5.1-8-9.6V6l8-3.2Z"/><path d="m9 12 2 2 4-4"/>',
  tasks: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 10h8m-8 4h6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  back: '<path d="M19 12H5m6 6-6-6 6-6"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2"/>',
  info: '<circle cx="12" cy="12" r="8"/><path d="M12 11v5m0-8h.01"/>',
  file: '<path d="M6 3h8l4 4v14H6zM14 3v5h4M9 13h6m-6 4h6"/>',
  download: '<path d="M12 4v11m-4-4 4 4 4-4M5 19h14"/>',
  slash: '<path d="M8 19 16 5"/>',
  circle: '<circle cx="12" cy="12" r="7"/>',
  up: '<path d="M12 19V5m-6 6 6-6 6 6"/>',
  down: '<path d="M12 5v14m-6-6 6 6 6-6"/>',
  chat: '<path d="M5 5h14v11h-8l-4 3v-3H5z"/>',
  sidebar: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M9 5v14"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none"/>',
  terminal: '<path d="m5 7 5 5-5 5m7 0h7"/>',
  model: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3v3m6-3v3m-6 12v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/><circle cx="12" cy="12" r="2"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  tool: '<path d="M14 6a4 4 0 0 0-5 5L4 16a2 2 0 0 0 3 3l5-5a4 4 0 0 0 5-5l-2 2-2-2z"/>',
  edit: '<path d="m14 6 4 4M5 19l4-1 10-10a2.83 2.83 0 0 0-4-4L5 14z"/>',
  checkCircle: '<circle cx="12" cy="12" r="8"/><path d="m8 12 2.7 2.7L16 9"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="6" r="2"/><path d="M6 7v10m12-9a7 7 0 0 1-7 7H6"/>',
  thinking: '<path d="m12 3 1.8 6.2L20 11l-6.2 1.8L12 19l-1.8-6.2L4 11l6.2-1.8L12 3Zm6 13v4m-2-2h4"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v4h-4"/>',
};

export function icon(name, size = 18) {
  const template = document.createElement('template');
  template.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size +
    '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    paths[name] + '</svg>';
  return template.content.firstChild;
}
