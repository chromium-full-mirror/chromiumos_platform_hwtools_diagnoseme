import { TestBed } from '@angular/core/testing';

import { ServodService } from "./servod-service"

describe('ServodService', () => {
  let service: ServodService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(ServodService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
