'use strict';

const paths = {
  compass: '<circle cx="12" cy="12" r="9"/><path d="m16 8-2 6-6 2 2-6z"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4m-4 8h4M17 8h4m-4 8h4"/>',
  bookmark: '<path d="M6 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17l-6-4-6 4z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  folder: '<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 9h18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  sort: '<path d="M4 6h16M4 12h11M4 18h6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>'
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.film}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const state = {user: null, movies: [], watchlist: [], progress: {}, page:'discover', genre:'All', query:'', sort:'featured', loaded:false};
let authMode = 'login';
let sourceMode = 'url';
let currentMovie = null;
let detailId = null;
let lastSaved = 0;
let playerLoadTimer;
let toastTimer;
let pendingDelete = null;
let pendingAuthAction = null;
let uploadBusy = false;
let loadVersion = 0;
const pendingSaves = new Set();

async function api(path, method='GET', data) {
  const response = await fetch(path, {method, credentials:'same-origin', headers: data === undefined ? {} : {'Content-Type':'application/json'}, body: data === undefined ? undefined : JSON.stringify(data)});
  let result;
  try { result = await response.json(); } catch { throw new Error('The server could not respond. Check that Movia is still running.'); }
  if (!response.ok) throw new Error(result.error || 'Something went wrong. Please try again.');
  return result;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3800);
}

function openDialog(id) {
  const dialog = $(id);
  if (!dialog.open) dialog.showModal();
}

function closeDialog(dialog) {
  if (dialog.id === 'add-dialog' && uploadBusy) return;
  if (dialog.id === 'player-dialog') saveProgress(true);
  dialog.close();
}

document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target.closest('[data-close]')) closeDialog(dialog);
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(dialog);
    }
  });
});
$('add-dialog').addEventListener('cancel', e => { if (uploadBusy) e.preventDefault(); });
$('player-dialog').addEventListener('cancel', () => saveProgress(true));
$('player-dialog').addEventListener('close', () => {
  clearTimeout(playerLoadTimer);
  $('video').pause();
  $('video').removeAttribute('src');
  $('video').load();
  currentMovie = null;
  render();
});

async function load() {
  const version = ++loadVersion;
  try {
    const data = await api('/api/bootstrap');
    if (version !== loadVersion) return;
    Object.assign(state, data, {loaded:true});
    $('account-label').textContent = state.user ? state.user.username : 'Sign in';
    $('film-count').textContent = state.movies.length;
    $('saved-count').textContent = state.watchlist.length;
    const hero = state.movies.find(movie => movie.id === 'sintel');
    if (hero) {
      $('hero-duration').textContent = `${hero.duration} min`;
    }
    render();
  } catch (error) {
    $('results-label').textContent = 'Your library could not be loaded.';
    showEmpty('Let’s reconnect.', error.message, 'Try again', load);
  }
}

function setPage(page) {
  if (!['discover','movies','watchlist','history','library'].includes(page)) page = 'discover';
  state.page = page;
  state.genre = 'All';
  state.query = '';
  $('search').value = '';
  history.replaceState(null, '', `#${page}`);
  render();
  window.scrollTo({top:0, behavior:'instant'});
}

document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => setPage(button.dataset.page)));
document.querySelectorAll('a[href="#discover"]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); setPage('discover'); }));
window.addEventListener('hashchange', () => setPage(location.hash.slice(1)));
$('search').addEventListener('input', event => { state.query = event.target.value.trim(); render(); });
$('sort').addEventListener('change', event => { state.sort = event.target.value; render(); });
$('filters').addEventListener('click', event => {
  const button = event.target.closest('[data-genre]');
  if (button) { state.genre = button.dataset.genre; render(); }
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !document.querySelector('dialog[open]') && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) {
    event.preventDefault(); $('search').focus();
  }
});

function showEmpty(title, description, action, handler) {
  $('film-grid').replaceChildren();
  $('empty-state').hidden = false;
  $('empty-title').textContent = title;
  $('empty-description').textContent = description;
  $('empty-action').textContent = action;
  $('empty-action').onclick = handler;
}

function render() {
  document.querySelectorAll('[data-page]').forEach(button => {
    const active = button.dataset.page === state.page;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('[data-genre]').forEach(button => {
    const active = state.genre === button.dataset.genre;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('hero').hidden = state.page !== 'discover' || !!state.query;
  $('library-banner').hidden = state.page === 'library';
  $('saved-count').textContent = state.watchlist.length;
  const savedHero = state.watchlist.includes('sintel');
  $('hero-save').innerHTML = `${icon(savedHero ? 'check' : 'plus')}${savedHero ? 'In my watchlist' : 'My watchlist'}`;
  const titles = {discover:'Find your next favorite',movies:'The open cinema collection',watchlist:'Saved for a great night',history:'Pick up where you left off',library:'Your stories. Your collection'};
  $('section-title').textContent = state.query ? `Results for “${state.query}”` : titles[state.page];
  const dot = document.createElement('span'); dot.textContent = '.'; $('section-title').append(dot);
  $('section-eyebrow').textContent = ({discover:'GOOD STORIES, NO SUBSCRIPTION',movies:'SMALL FILMS. BIG IMAGINATION.',watchlist:'YOUR WATCHLIST',history:'CONTINUE WATCHING',library:'YOUR PERSONAL LIBRARY'})[state.page];
  if (!state.loaded) return;
  if (!state.user && ['watchlist','history','library'].includes(state.page)) {
    $('results-label').textContent = 'A little space for your kind of films.';
    showEmpty('Make yourself at home.', 'Create a free local account to save films, keep your place, and build your own collection.', 'Sign in or create an account', () => openAuth());
    return;
  }
  let films = state.movies.filter(movie => {
    if (state.page === 'watchlist' && !state.watchlist.includes(movie.id)) return false;
    if (state.page === 'library' && movie.owner_id !== state.user?.id) return false;
    if (state.page === 'history') {
      const progress = state.progress[movie.id];
      if (!progress || progress.position < 1 || progress.position >= progress.duration - 5) return false;
    }
    return (state.genre === 'All' || movie.genre === state.genre) && (!state.query || `${movie.title} ${movie.genre} ${movie.description}`.toLowerCase().includes(state.query.toLowerCase()));
  });
  films.sort((a,b) => {
    if (state.sort === 'title') return a.title.localeCompare(b.title);
    if (state.sort === 'newest') return b.year-a.year;
    if (state.sort === 'shortest') return (a.duration || Infinity)-(b.duration || Infinity);
    if (state.page === 'history') return (state.progress[b.id]?.updated || 0)-(state.progress[a.id]?.updated || 0);
    const order = ['sintel','big-buck-bunny','tears-of-steel','elephants-dream'];
    const rank = m => { const index = order.indexOf(m.id); return index < 0 ? 99 : index; };
    return rank(a)-rank(b);
  });
  $('results-label').textContent = `${films.length} ${films.length === 1 ? 'film' : 'films'}${state.genre !== 'All' ? ` · ${state.genre}` : ''}${state.page === 'discover' || state.page === 'movies' ? ' · A world of independent imagination' : ''}`;
  if (!films.length) {
    if (state.query || state.genre !== 'All') showEmpty('No films found.', 'Try another title or explore a different genre.', 'Clear filters', () => { state.query='';state.genre='All';$('search').value='';render(); });
    else if (state.page === 'watchlist') showEmpty('Your next movie night starts here.', 'Tap the bookmark on any film to save it for later.', 'Explore films', () => setPage('movies'));
    else if (state.page === 'history') showEmpty('A fresh start.', 'Start a film and your place will be saved here automatically.', 'Explore films', () => setPage('movies'));
    else showEmpty('Every collection starts with one film.', 'Upload a video or add a direct link to a film you have permission to use.', 'Add your first film', openAdd);
    return;
  }
  $('empty-state').hidden = true;
  $('film-grid').innerHTML = films.map(movie => {
    const saved = state.watchlist.includes(movie.id);
    const progress = state.progress[movie.id];
    return `<article class="film-card"><div class="poster-wrap"><button class="poster-button" data-details="${escapeHtml(movie.id)}" aria-label="View ${escapeHtml(movie.title)}"><img src="${escapeHtml(movie.image)}" alt="${escapeHtml(movie.title)} film artwork" loading="lazy"><span class="poster-shade"></span><span class="poster-label">${movie.owner_id ? 'YOUR LIBRARY' : 'OPEN FILM'}</span><span class="poster-title">${escapeHtml(movie.title)}</span><span class="card-play">${icon('play')}</span></button><button class="save-button ${saved ? 'saved' : ''}" data-save="${escapeHtml(movie.id)}" aria-label="${saved ? 'Remove' : 'Save'} ${escapeHtml(movie.title)} ${saved ? 'from' : 'to'} watchlist" aria-pressed="${saved}">${icon(saved ? 'check' : 'bookmark')}</button>${progress?.duration ? `<progress class="film-progress" value="${progress.position}" max="${progress.duration}" aria-label="Viewing progress"></progress>` : ''}</div><div class="card-info"><div class="card-heading"><h3 class="card-title">${escapeHtml(movie.title)}</h3><span class="card-duration">${movie.duration ? `${movie.duration} min` : 'Your film'}</span></div><p><span>${movie.year}</span><span>·</span><span>${escapeHtml(movie.genre)}</span></p></div></article>`;
  }).join('');
  $('film-grid').querySelectorAll('img').forEach(image => { image.addEventListener('error', () => { image.src='/assets/placeholder.svg'; }, {once:true}); });
}

$('film-grid').addEventListener('click', event => {
  const save = event.target.closest('[data-save]');
  const details = event.target.closest('[data-details]');
  if (save) toggleSave(save.dataset.save);
  else if (details) showDetails(details.dataset.details);
});

function requireUser(action) {
  if (state.user) { action(); return; }
  pendingAuthAction = action;
  openAuth();
}

async function toggleSave(id) {
  if (!state.user) { requireUser(() => toggleSave(id)); return; }
  if (pendingSaves.has(id)) return;
  pendingSaves.add(id);
  try {
    const saved = state.watchlist.includes(id);
    await api(`/api/watchlist/${id}`, saved ? 'DELETE' : 'PUT', {});
    state.watchlist = saved ? state.watchlist.filter(movieId => movieId !== id) : [...state.watchlist,id];
    render();
    if ($('details-dialog').open && detailId === id) renderDetails(id);
    toast(saved ? 'Removed from your watchlist.' : 'Saved for your next movie night.');
  } catch (error) { toast(error.message); }
  finally { pendingSaves.delete(id); }
}

function renderDetails(id) {
  const movie = state.movies.find(item => item.id === id);
  if (!movie) return;
  const saved = state.watchlist.includes(id);
  const progress = state.progress[id];
  const resume = progress && progress.position > 1 && progress.position < progress.duration-5;
  $('details-content').innerHTML = `<img class="details-image" src="${escapeHtml(movie.image)}" alt="${escapeHtml(movie.title)}"><div class="details-body"><h2>${escapeHtml(movie.title)}</h2><div class="film-meta"><span>${movie.year}</span><i></i><span>${escapeHtml(movie.genre)}</span>${movie.duration ? `<i></i><span>${movie.duration} min</span>` : ''}<span class="quality">${movie.owner_id ? 'YOUR LIBRARY' : 'OPEN FILM'}</span></div><p class="description">${escapeHtml(movie.description || 'A film from your personal collection.')}</p><div class="hero-actions"><button class="primary-button" data-watch="${escapeHtml(id)}">${icon('play')}${resume ? `Resume at ${formatTime(progress.position)}` : 'Watch now'}</button><button class="secondary-button" data-save="${escapeHtml(id)}">${icon(saved ? 'check' : 'plus')}${saved ? 'In my watchlist' : 'My watchlist'}</button></div><p class="details-credit">${escapeHtml(movie.credit)}<br>${escapeHtml(movie.license)}${movie.source ? ` · <a href="${escapeHtml(movie.source)}" target="_blank" rel="noopener noreferrer">Film & credits ↗</a>` : ''}</p>${movie.owner_id === state.user?.id ? `<button class="delete-film" data-delete="${escapeHtml(id)}">Remove from my library</button>` : ''}</div>`;
  $('details-content').querySelector('img').addEventListener('error', event => { event.target.src='/assets/placeholder.svg'; }, {once:true});
}

function showDetails(id) { detailId=id;renderDetails(id);openDialog('details-dialog'); }
$('details-content').addEventListener('click', event => {
  const watch = event.target.closest('[data-watch]');
  const save = event.target.closest('[data-save]');
  const remove = event.target.closest('[data-delete]');
  if (watch) { $('details-dialog').close(); play(watch.dataset.watch); }
  if (save) toggleSave(save.dataset.save);
  if (remove) { pendingDelete=remove.dataset.delete;openDialog('confirm-dialog'); }
});
$('confirm-delete').addEventListener('click', async () => {
  if (!pendingDelete) return;
  $('confirm-delete').disabled = true;
  try {
    await api(`/api/movies/${pendingDelete}`, 'DELETE', {});
    $('confirm-dialog').close();$('details-dialog').close();
    pendingDelete=null; await load(); toast('Film removed from your library.');
  } catch (error) { $('confirm-dialog').close();toast(error.message); }
  finally { $('confirm-delete').disabled=false; }
});

function formatTime(seconds) { return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`; }
function play(id) {
  const movie = state.movies.find(item => item.id === id);
  if (!movie) return;
  currentMovie = movie;
  lastSaved = 0;
  $('player-title').textContent = movie.title;
  $('player-credit').textContent = movie.credit;
  $('progress-note').textContent = state.user ? 'Your place is saved automatically.' : 'Sign in to save your progress.';
  $('player-error').hidden = true;
  $('video').poster = movie.image;
  $('video').src = movie.video_url;
  clearTimeout(playerLoadTimer);
  playerLoadTimer = setTimeout(() => {
    if ($('player-dialog').open && $('video').readyState < 2) {
      $('player-error').hidden = false;
      $('player-error').textContent = 'Still connecting to the video source. External film hosts can be slow or unavailable. You can try again later or play a video from your own library.';
    }
  }, 15000);
  openDialog('player-dialog');
  $('video').play().catch(() => {});
}
$('video').addEventListener('loadedmetadata', () => {
  if (!currentMovie) return;
  const saved = state.progress[currentMovie.id];
  if (saved && saved.position > 1 && saved.position < $('video').duration-5) $('video').currentTime = saved.position;
});
$('video').addEventListener('loadeddata', () => {
  clearTimeout(playerLoadTimer);
  $('player-error').hidden = true;
});
$('video').addEventListener('error', () => {
  clearTimeout(playerLoadTimer);
  if (!currentMovie || !$('video').getAttribute('src')) return;
  $('player-error').hidden = false;
  $('player-error').textContent = 'This video could not be played. Check your internet connection, or try again later. For your own files, use an MP4 with H.264 video or a browser-supported WebM.';
});
$('video').addEventListener('timeupdate', () => saveProgress(false));
$('video').addEventListener('pause', () => saveProgress(true));
$('video').addEventListener('ended', () => saveProgress(true));
document.addEventListener('visibilitychange', () => { if (document.hidden) saveProgress(true); });
window.addEventListener('pagehide', () => saveProgress(true));
function saveProgress(force) {
  const video = $('video');
  if (!currentMovie || !state.user || !Number.isFinite(video.duration) || video.duration <= 0 || video.currentTime <= 0) return;
  if (!force && Date.now()-lastSaved < 5000) return;
  const id = currentMovie.id;
  const body = {position:Math.min(video.currentTime, video.duration),duration:video.duration};
  lastSaved = Date.now();
  state.progress[id] = {...body, updated:Date.now()/1000};
  fetch(`/api/progress/${id}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),keepalive:true}).then(response => {
    if (!response.ok) throw new Error();
    $('progress-note').textContent = 'Progress saved.';
  }).catch(() => { $('progress-note').textContent = 'Progress could not be saved. Check your connection.'; });
}
$('hero-play').addEventListener('click', () => play('sintel'));
$('hero-info').addEventListener('click', () => showDetails('sintel'));
$('hero-save').addEventListener('click', () => toggleSave('sintel'));

function openAuth() { updateAuth();openDialog('auth-dialog'); }
function updateAuth() {
  const register = authMode === 'register';
  $('auth-title').textContent = register ? 'Make yourself at home.' : 'Your seat is waiting.';
  $('auth-description').textContent = register ? 'Create a free account for your very own film collection.' : 'Sign in to keep your watchlist and pick up where you left off.';
  $('auth-submit').textContent = register ? 'Create free account' : 'Sign in';
  $('auth-switch').textContent = register ? 'Already have an account? Sign in' : 'New here? Create a free account';
  $('auth-form').elements.password.autocomplete = register ? 'new-password' : 'current-password';
  $('auth-error').textContent = '';
}
$('auth-switch').addEventListener('click', () => { authMode = authMode === 'login' ? 'register' : 'login'; updateAuth(); });
$('auth-dialog').addEventListener('close', () => { $('auth-form').reset(); if (!state.user) pendingAuthAction=null; });
$('account-button').addEventListener('click', () => {
  if (!state.user) { openAuth();return; }
  $('account-name').textContent = `Hello, ${state.user.username}.`;
  openDialog('account-dialog');
});
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('auth-error').textContent = '';
  $('auth-submit').disabled = true;
  $('auth-switch').disabled = true;
  try {
    const data = Object.fromEntries(new FormData(event.target));
    const result = await api(`/api/${authMode}`, 'POST', data);
    state.user = result.user;
    $('auth-dialog').close();
    await load();
    toast(`Welcome ${state.user.username}. Make it a movie night.`);
    const action = pendingAuthAction; pendingAuthAction=null;
    if (action) action();
  } catch (error) { $('auth-error').textContent = error.message; }
  finally { $('auth-submit').disabled=false;$('auth-switch').disabled=false; }
});
$('logout-button').addEventListener('click', async () => {
  try {
    await api('/api/logout', 'POST', {});
    $('account-dialog').close();
    state.user=null;state.watchlist=[];state.progress={};
    await load();toast('Signed out. See you next movie night.');
  } catch (error) { toast(error.message); }
});

function openAdd() { requireUser(() => { $('add-error').textContent='';openDialog('add-dialog'); }); }
$('add-sidebar').addEventListener('click', openAdd);
$('add-banner').addEventListener('click', openAdd);
document.querySelectorAll('[data-source]').forEach(button => button.addEventListener('click', () => {
  sourceMode = button.dataset.source;
  document.querySelectorAll('[data-source]').forEach(tab => { tab.classList.toggle('active', tab.dataset.source === sourceMode);tab.setAttribute('aria-pressed', String(tab.dataset.source === sourceMode)); });
  $('url-field').hidden = sourceMode !== 'url';
  $('file-field').hidden = sourceMode !== 'file';
  $('add-form').elements.video_url.required = sourceMode === 'url';
  $('add-form').elements.video_url.disabled = sourceMode !== 'url';
  $('add-form').elements.video_file.required = sourceMode === 'file';
}));
function uploadFile(file, metadata) {
  return new Promise((resolve,reject) => {
    const request = new XMLHttpRequest();
    request.open('POST','/api/upload');
    request.setRequestHeader('X-Movia-Metadata', encodeURIComponent(JSON.stringify({...metadata,filename:file.name})));
    request.setRequestHeader('Content-Type','application/octet-stream');
    request.upload.onprogress = event => {
      if (event.lengthComputable) {
        const percent = Math.round(event.loaded/event.total*100);
        $('upload-progress').value=percent;
        $('add-submit').textContent = percent === 100 ? 'Saving your film…' : `Uploading… ${percent}%`;
      }
    };
    request.onload = () => {
      let result;
      try {result=JSON.parse(request.responseText);} catch {reject(new Error('Upload failed. Please try again.'));return;}
      if (request.status >= 200 && request.status < 300) resolve(result); else reject(new Error(result.error || 'Upload failed.'));
    };
    request.onerror = () => reject(new Error('The upload was interrupted. Check that Movia is running and try again.'));
    request.send(file);
  });
}
$('add-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  data.rights = form.elements.rights.checked;
  delete data.video_file;
  $('add-error').textContent='';
  const file = form.elements.video_file.files[0];
  if (sourceMode === 'file' && (!file || file.size > 512*1024*1024 || file.size === 0)) { $('add-error').textContent='Choose a nonempty video file under 512 MB.';return; }
  uploadBusy=true;
  const controls = [...form.querySelectorAll('input,textarea,select,button'), ...$('add-dialog').querySelectorAll('[data-close]')];
  controls.forEach(control => { control.disabled=true; });
  $('add-submit').textContent='Adding film…';
  try {
    if (sourceMode === 'file') { $('upload-progress').hidden=false;$('upload-progress').value=0;await uploadFile(file,data); }
    else await api('/api/movies', 'POST', data);
    uploadBusy=false;$('add-dialog').close();form.reset();
    await load();setPage('library');toast('Your film is ready in your library.');
  } catch (error) { $('add-error').textContent=error.message; }
  finally { uploadBusy=false;controls.forEach(control => {control.disabled=false;});form.elements.video_url.disabled=sourceMode !== 'url';$('add-submit').textContent='Add to my library';$('upload-progress').hidden=true; }
});

function showCredits() {
  $('credits-list').innerHTML=state.movies.filter(movie => !movie.owner_id).map(movie => `<div class="credit-item"><a href="${escapeHtml(movie.source)}" target="_blank" rel="noopener noreferrer">${escapeHtml(movie.title)} ↗</a><p>${escapeHtml(movie.credit)} · ${escapeHtml(movie.license)}</p></div>`).join('');
  openDialog('about-dialog');
}
['about-button','credits-footer'].forEach(id => $(id).addEventListener('click', showCredits));
state.page = ['discover','movies','watchlist','history','library'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'discover';
load();
