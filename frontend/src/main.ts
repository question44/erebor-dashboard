import './background';
import { Component, inject } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { MatButtonModule } from '@angular/material/button';
import { PublicSettings } from './config';
import { Dashboard } from './dashboard';
import { Icon } from './icon';
import { Session } from './session';
import { Login, Setup } from './sign-in';
import { Users } from './users';

const publicSettings: PublicSettings = await fetch('/config.json').then(response => {
  if (!response.ok) throw new Error('Dashboard configuration unavailable');
  return response.json();
});

@Component({
  selector: 'erebor-app',
  imports: [Dashboard, Login, Setup, Users, Icon, MatButtonModule],
  template: `
@switch (view().kind) {
  @case ('dashboard') { <erebor-dashboard /> }
  @case ('users') { <erebor-users /> }
  @case ('setup') { <erebor-setup /> }
  @case ('login') { <erebor-login [message]="loginMessage()" /> }
  @case ('unavailable') {
    <main class="auth-page"><div class="auth-card">
      <div class="auth-mark"><erebor-icon name="power" /></div>
      <h1>{{ session.branding.title }}</h1>
      <p class="intro" role="status">{{ unavailableMessage() }}</p>
      <button mat-flat-button (click)="session.check()">Try again</button>
    </div></main>
  }
  @default { <main class="auth-page"><p class="intro" role="status">Loading…</p></main> }
}`,
})
class App {
  readonly session = inject(Session);
  readonly view = this.session.view;

  constructor() {
    const scheme = matchMedia('(prefers-color-scheme: dark)');
    const update = () => document.querySelector('meta[name="theme-color"]')?.setAttribute('content', scheme.matches ? '#141218' : '#fdf8ff');
    update();
    scheme.addEventListener('change', update);
    document.title = `${publicSettings.title} services`;
    void this.session.start(publicSettings);
  }

  loginMessage() { const view = this.view(); return view.kind === 'login' ? view.message : undefined; }
  unavailableMessage() { const view = this.view(); return view.kind === 'unavailable' ? view.message : ''; }
}

bootstrapApplication(App).catch(error => console.error('Dashboard unavailable:', error));
if ('serviceWorker' in navigator && isSecureContext) {
  navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
    .catch(error => console.error('Offline support unavailable:', error));
}
