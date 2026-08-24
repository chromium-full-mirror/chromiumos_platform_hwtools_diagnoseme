// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ElementRef} from '@angular/core';

/**
 * Smoothly scrolls to the targeted card DOM element and sets focus on the given action button.
 */
export function autoAdvanceToCard(
  cardElementId: string,
  buttonRef?: ElementRef<HTMLButtonElement | HTMLInputElement>,
  delayMs = 500,
): void {
  setTimeout(() => {
    const el = document.getElementById(cardElementId);
    el?.scrollIntoView({behavior: 'smooth', block: 'center'});
    buttonRef?.nativeElement?.focus();
  }, delayMs);
}
