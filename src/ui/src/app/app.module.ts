// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {NgModule} from '@angular/core';
import {RouterModule} from '@angular/router'; // Import RouterModule

import {AppRoutingModule} from './app-routing.module';
import {AppComponent} from './app';
import {provideAnimationsAsync} from '@angular/platform-browser/animations/async';

import {MatSlideToggleModule} from '@angular/material/slide-toggle';
import {MatToolbarModule} from '@angular/material/toolbar';
import {MatIconModule} from '@angular/material/icon';
import {MatMenuModule} from '@angular/material/menu';
import {MatCardModule} from '@angular/material/card';

import {ServicesModule} from './services/services.module';

import {MatDividerModule} from '@angular/material/divider';
import {MatButtonModule} from '@angular/material/button';

import {MatInputModule} from '@angular/material/input';
import {MatSelectModule} from '@angular/material/select';
import {MatFormFieldModule} from '@angular/material/form-field';
import {DolosProvisionComponent} from './dolos-provision/dolos-provision';

import {MatProgressBarModule} from '@angular/material/progress-bar';

import {MatGridListModule} from '@angular/material/grid-list';

import {MatCheckboxModule} from '@angular/material/checkbox';
import {FormsModule} from '@angular/forms';

import {MatAutocompleteModule} from '@angular/material/autocomplete';

import {MatIconRegistry} from '@angular/material/icon';
import {ServoV4p1ManufacturingComponent} from './servo-v4p1-manufacturing/servo-v4p1-manufacturing';
import {ServoMicroManufacturingComponent} from './servo-micro-manufacturing/servo-micro-manufacturing';

@NgModule({
  imports: [
    AppComponent,
    AppRoutingModule,
    RouterModule, // Add RouterModule here
    MatSlideToggleModule,
    DolosProvisionComponent,
    MatToolbarModule,
    MatIconModule,
    MatMenuModule,
    MatCardModule,
    ServicesModule.forRoot(),
    MatButtonModule,
    MatDividerModule,
    MatInputModule,
    MatSelectModule,
    MatFormFieldModule,
    MatProgressBarModule,
    MatGridListModule,
    MatCheckboxModule,
    FormsModule,
    MatAutocompleteModule,
    ServoV4p1ManufacturingComponent,
    ServoMicroManufacturingComponent,
  ],
  providers: [provideAnimationsAsync()],
})
export class AppModule {
  constructor(iconRegistry: MatIconRegistry) {
    iconRegistry.setDefaultFontSetClass('material-symbols-outlined');
    // Or for other styles: 'material-symbols-rounded', 'material-symbols-sharp'
  }
}
