import { Component, OnInit, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MODULES, actionLabel, api } from './config';
import { Icon } from './icon';
import { Session } from './session';
import { USERNAME_PATTERN, passwordsMatch } from './sign-in';

interface Permissions { modules: string[]; services: string[]; actions: string[]; }
interface User { username: string; admin: boolean; permissions: Permissions; }
interface Catalog { modules: string[]; actions: string[]; services: { id: string; label: string }[]; }
interface Directory { catalog: Catalog; users: User[]; }
type Group = keyof Permissions;
interface EditorData { catalog: Catalog; user: User | null; self: string; }

const NO_PERMISSIONS = (): Permissions => ({ modules: [], services: [], actions: [] });

@Component({
  selector: 'erebor-users',
  imports: [MatButtonModule, MatDialogModule, Icon],
  template: `
<main>
  <header>
    <div class="host-heading">
      <button mat-button class="back-button" (click)="session.show('dashboard')" aria-label="Back to dashboard">
        <span class="account-content"><erebor-icon name="back" />Dashboard</span>
      </button>
    </div>
    <button mat-flat-button (click)="edit(null)" [disabled]="!directory()"><span class="account-content"><erebor-icon name="plus" />Add user</span></button>
  </header>
  <h1 class="page-title">Users</h1>
  <p class="intro">Choose which modules, services and actions each person sees. Administrators always see everything and can manage users.
    The server enforces actions; hiding a service removes its link but does not sign anyone out of that service.</p>
  @if (error()) { <p class="notice error" role="alert">{{ error() }}</p> }
  @if (directory(); as dir) {
    <div class="user-list">
      @for (user of dir.users; track user.username) {
        <div class="user-card">
          <div class="service-icon"><erebor-icon [name]="user.admin ? 'shield' : 'user'" /></div>
          <div class="service-text">
            <span class="service-title">{{ user.username }}@if (user.username === session.account?.username) { <span class="you"> (you)</span> }</span>
            <span class="description">{{ summary(user, dir.catalog) }}</span>
          </div>
          <div class="user-actions">
            <button mat-button (click)="edit(user)" [attr.aria-label]="'Edit ' + user.username"><erebor-icon name="edit" /></button>
            <button mat-button class="danger-text" (click)="remove(user)" [disabled]="user.username === session.account?.username"
              [attr.aria-label]="'Delete ' + user.username"><erebor-icon name="trash" /></button>
          </div>
        </div>
      }
    </div>
  } @else if (!error()) {
    <p class="intro">Loading users…</p>
  }
</main>`,
})
export class Users implements OnInit {
  readonly session = inject(Session);
  readonly dialog = inject(MatDialog);
  readonly directory = signal<Directory | null>(null);
  readonly error = signal('');

  ngOnInit() { void this.load(); }

  async load() {
    try {
      this.directory.set(await api<Directory>('/users'));
      this.error.set('');
    } catch (error) { this.fail(error); }
  }

  private fail(error: unknown) {
    if (error instanceof Error && 'status' in error && error.status === 401) { this.session.expired(); return; }
    this.error.set(error instanceof Error ? error.message : 'Request failed');
  }

  summary(user: User, catalog: Catalog) {
    if (user.admin) return 'Administrator · everything';
    const p = user.permissions;
    const known = (ids: string[], allowed: string[]) => ids.filter(id => allowed.includes(id)).length;
    const parts = [`${known(p.modules, catalog.modules)}/${catalog.modules.length} modules`];
    if (p.modules.includes('services')) parts.push(`${known(p.services, catalog.services.map(s => s.id))}/${catalog.services.length} services`);
    if (p.modules.includes('quick-actions')) parts.push(`${known(p.actions, catalog.actions)}/${catalog.actions.length} actions`);
    return parts.join(' · ');
  }

  edit(user: User | null) {
    const directory = this.directory();
    if (!directory) return;
    const data: EditorData = { catalog: directory.catalog, user, self: this.session.account?.username ?? '' };
    this.dialog.open(UserEditor, { data, maxWidth: 'calc(100vw - 32px)', width: '560px', autoFocus: 'first-tabbable' })
      .afterClosed().subscribe(saved => {
        if (!saved) return;
        void this.load();
        // Your own permissions may have changed what the dashboard shows.
        if (user?.username === data.self) void this.session.loadDashboard();
      });
  }

  async remove(user: User) {
    if (!confirm(`Delete ${user.username}? They are signed out immediately.`)) return;
    try {
      await api(`/users/${encodeURIComponent(user.username)}`, 'DELETE');
      await this.load();
    } catch (error) { this.fail(error); }
  }
}

@Component({
  selector: 'erebor-user-editor',
  imports: [ReactiveFormsModule, MatButtonModule, MatCheckboxModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSlideToggleModule],
  template: `
<h2 mat-dialog-title>{{ data.user ? 'Edit ' + data.user.username : 'Add user' }}</h2>
<mat-dialog-content>
  <form [formGroup]="form" class="auth-form" id="user-form" (ngSubmit)="save()">
    @if (!data.user) {
      <mat-form-field appearance="outline">
        <mat-label>Username</mat-label>
        <input matInput formControlName="username" autocomplete="off" autocapitalize="none" spellcheck="false" required>
        @if (form.controls.username.hasError('pattern')) { <mat-error>Use up to 32 letters, digits, dots, dashes or underscores.</mat-error> }
      </mat-form-field>
    }
    <mat-form-field appearance="outline">
      <mat-label>{{ data.user ? 'New password (optional)' : 'Password' }}</mat-label>
      <input matInput type="password" formControlName="password" autocomplete="new-password" [required]="!data.user">
      <mat-hint>{{ data.user ? 'Leave empty to keep the current password. Changing it signs the user out elsewhere.' : 'At least 8 characters.' }}</mat-hint>
      @if (form.controls.password.hasError('minlength')) { <mat-error>Use at least 8 characters.</mat-error> }
    </mat-form-field>
    <mat-slide-toggle formControlName="admin">Administrator</mat-slide-toggle>
    @if (isSelf) { <p class="hint">You cannot remove your own administrator access.</p> }
  </form>
  @if (form.controls.admin.value) {
    <p class="hint">Administrators see every module, service and action.</p>
  } @else {
    <fieldset class="permission-group">
      <legend>Modules</legend>
      @for (id of data.catalog.modules; track id) {
        <mat-checkbox [checked]="has('modules', id)" (change)="toggle('modules', id, $event.checked)">
          {{ moduleInfo[id]?.label ?? id }}<span class="hint block">{{ moduleInfo[id]?.description }}</span>
        </mat-checkbox>
      }
    </fieldset>
    <fieldset class="permission-group" [disabled]="!has('modules', 'services')">
      <legend>Services @if (!has('modules', 'services')) { <span class="hint">— enable the Services module</span> }</legend>
      @for (service of data.catalog.services; track service.id) {
        <mat-checkbox [checked]="has('services', service.id)" [disabled]="!has('modules', 'services')"
          (change)="toggle('services', service.id, $event.checked)">{{ service.label }}</mat-checkbox>
      } @empty { <p class="hint">No services are configured.</p> }
      <div class="group-tools"><button mat-button type="button" [disabled]="!has('modules', 'services')" (click)="all('services', true)">All</button>
        <button mat-button type="button" [disabled]="!has('modules', 'services')" (click)="all('services', false)">None</button></div>
    </fieldset>
    <fieldset class="permission-group" [disabled]="!has('modules', 'quick-actions')">
      <legend>Actions @if (!has('modules', 'quick-actions')) { <span class="hint">— enable the Quick actions module</span> }</legend>
      @for (id of data.catalog.actions; track id) {
        <mat-checkbox [checked]="has('actions', id)" [disabled]="!has('modules', 'quick-actions')"
          (change)="toggle('actions', id, $event.checked)">{{ label(id) }}</mat-checkbox>
      } @empty { <p class="hint">No actions are configured.</p> }
      <div class="group-tools"><button mat-button type="button" [disabled]="!has('modules', 'quick-actions')" (click)="all('actions', true)">All</button>
        <button mat-button type="button" [disabled]="!has('modules', 'quick-actions')" (click)="all('actions', false)">None</button></div>
    </fieldset>
  }
  @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
</mat-dialog-content>
<mat-dialog-actions align="end">
  <button mat-button [mat-dialog-close]="false">Cancel</button>
  <button mat-flat-button type="submit" form="user-form" [disabled]="form.invalid || busy()">{{ busy() ? 'Saving…' : 'Save' }}</button>
</mat-dialog-actions>`,
})
export class UserEditor {
  readonly data: EditorData = inject(MAT_DIALOG_DATA);
  readonly ref = inject(MatDialogRef<UserEditor>);
  readonly moduleInfo = MODULES;
  readonly label = actionLabel;
  readonly isSelf = this.data.user?.username === this.data.self;
  readonly busy = signal(false);
  readonly error = signal('');
  readonly permissions = signal<Permissions>(structuredClone(this.data.user?.permissions ?? NO_PERMISSIONS()));
  readonly form = inject(FormBuilder).nonNullable.group({
    username: [this.data.user?.username ?? '', this.data.user ? [] : [Validators.required, Validators.pattern(USERNAME_PATTERN)]],
    password: ['', this.data.user ? [Validators.minLength(8), Validators.maxLength(256)]
      : [Validators.required, Validators.minLength(8), Validators.maxLength(256)]],
    admin: [{ value: this.data.user?.admin ?? false, disabled: this.isSelf }],
  });

  has(group: Group, id: string) { return this.permissions()[group]?.includes(id) ?? false; }

  toggle(group: Group, id: string, on: boolean) {
    this.permissions.update(p => ({ ...p, [group]: on ? [...new Set([...(p[group] ?? []), id])] : (p[group] ?? []).filter(v => v !== id) }));
  }

  all(group: 'services' | 'actions', on: boolean) {
    const ids = group === 'services' ? this.data.catalog.services.map(s => s.id) : this.data.catalog.actions;
    this.permissions.update(p => ({ ...p, [group]: on ? [...ids] : [] }));
  }

  async save() {
    if (this.form.invalid || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    const { username, password, admin } = this.form.getRawValue();
    try {
      if (this.data.user) {
        await api(`/users/${encodeURIComponent(this.data.user.username)}`, 'PUT', {
          ...(password ? { password } : {}), ...(this.isSelf ? {} : { admin }), permissions: this.permissions(),
        });
      } else {
        await api('/users', 'POST', { username: username.trim().toLowerCase(), password, admin, permissions: this.permissions() });
      }
      this.ref.close(true);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : 'Could not save');
    } finally {
      this.busy.set(false);
    }
  }
}

@Component({
  selector: 'erebor-password-dialog',
  imports: [ReactiveFormsModule, MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  template: `
<h2 mat-dialog-title>Change password</h2>
<mat-dialog-content>
  <form [formGroup]="form" class="auth-form" id="password-form" (ngSubmit)="save()">
    <mat-form-field appearance="outline">
      <mat-label>Current password</mat-label>
      <input matInput type="password" formControlName="current" autocomplete="current-password" required>
    </mat-form-field>
    <mat-form-field appearance="outline">
      <mat-label>New password</mat-label>
      <input matInput type="password" formControlName="password" autocomplete="new-password" required>
      <mat-hint>At least 8 characters. Your other devices will be signed out.</mat-hint>
    </mat-form-field>
    <mat-form-field appearance="outline">
      <mat-label>Confirm new password</mat-label>
      <input matInput type="password" formControlName="confirm" autocomplete="new-password" required>
    </mat-form-field>
    @if (form.hasError('mismatch') && form.controls.confirm.touched) { <p class="form-error" role="alert">Passwords do not match.</p> }
    @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
  </form>
</mat-dialog-content>
<mat-dialog-actions align="end">
  <button mat-button [mat-dialog-close]="false">Cancel</button>
  <button mat-flat-button type="submit" form="password-form" [disabled]="form.invalid || busy()">{{ busy() ? 'Saving…' : 'Change password' }}</button>
</mat-dialog-actions>`,
})
export class PasswordDialog {
  readonly ref = inject(MatDialogRef<PasswordDialog>);
  readonly session = inject(Session);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = inject(FormBuilder).nonNullable.group({
    current: ['', Validators.required],
    password: ['', [Validators.required, Validators.minLength(8), Validators.maxLength(256)]],
    confirm: ['', Validators.required],
  }, { validators: passwordsMatch });

  async save() {
    if (this.form.invalid || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      const { current, password } = this.form.getRawValue();
      await api('/auth/password', 'POST', { current, password });
      this.ref.close(true);
    } catch (error) {
      if (error instanceof Error && 'status' in error && error.status === 401) { this.ref.close(); this.session.expired(); return; }
      this.error.set(error instanceof Error ? error.message : 'Could not change password');
    } finally {
      this.busy.set(false);
    }
  }
}
