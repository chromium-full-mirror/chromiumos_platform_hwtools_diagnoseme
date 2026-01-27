// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {bootstrapApplication, BrowserModule} from '@angular/platform-browser';
import {importProvidersFrom} from '@angular/core';
import {provideAnimationsAsync} from '@angular/platform-browser/animations/async';
import {RouterModule} from '@angular/router';

import {AppComponent} from './app/app';
import {AppRoutingModule} from './app/app-routing.module';
import {ServicesModule} from './app/services/services.module';

bootstrapApplication(AppComponent, {
  providers: [
    importProvidersFrom(
      BrowserModule,
      AppRoutingModule,
      RouterModule,
      ServicesModule.forRoot(),
    ),
    provideAnimationsAsync(),
  ],
}).catch(err => console.error(err));
