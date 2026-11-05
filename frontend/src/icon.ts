import { Component, Input } from '@angular/core';
import { paths } from './config';

@Component({
  selector: 'erebor-icon',
  template: `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path [attr.d]="paths[name]" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>`,
  styles: [`:host { display: inline-flex; width: 24px; height: 24px; flex-shrink: 0; } svg { width: 100%; height: 100%; }`],
})
export class Icon {
  @Input({ required: true }) name = '';
  readonly paths = paths;
}
