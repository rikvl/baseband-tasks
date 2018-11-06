# Licensed under the GPLv3 - see LICENSE
import pytest
import numpy as np
import astropy.units as u
from astropy.time import Time
from astropy.tests.helper import assert_quantity_allclose

from ..dispersion import Disperse, Dedisperse, DispersionMeasure
from ..generators import StreamGenerator


REFERENCE_FREQUENCIES = (
    None,  # Default, will use mean
    300 * u.MHz,  # Centre frequency
    300.0123456789 * u.MHz,  # More random.
    300.064 * u.MHz,  # Upper edge
    299.936 * u.MHz,  # Lower edge
    300.128 * u.MHz,  # Above upper edge
    300.123456789 * u.MHz,  # More random, above upper edge
    299.872 * u.MHz)  # Below lower edge


class TestDispersion:

    def setup(self):
        self.start_time = Time('2010-11-12T13:14:15')
        self.sample_rate = 128. * u.kHz
        self.shape = (164000, 2)
        self.gp_sample = 64000
        self.gp = StreamGenerator(self.make_giant_pulse,
                                  shape=self.shape, start_time=self.start_time,
                                  sample_rate=self.sample_rate,
                                  samples_per_frame=1000, dtype=np.complex64,
                                  frequency=300*u.MHz,
                                  sideband=np.array((1, -1)))
        # Time delay of 0.05 s over 128 kHz band.
        self.dm = DispersionMeasure(1000.*0.05/0.039342251)

    def make_giant_pulse(self, sh):
        data = np.empty((sh.samples_per_frame,) + sh.shape[1:], sh.dtype)
        do_gp = (sh.tell() + np.arange(sh.samples_per_frame) ==
                 self.gp_sample)
        data[...] = do_gp[:, np.newaxis]
        return data

    def test_time_delay(self):
        time_delay = self.dm.time_delay(
            self.gp.frequency - self.sample_rate / 2.,
            self.gp.frequency + self.sample_rate / 2.)
        assert abs(time_delay - 0.05 * u.s) < 1. * u.ns

    def test_giant_pulse(self):
        data = self.gp.read()
        assert np.allclose(data, np.where(
            np.arange(data.shape[0])[:, np.newaxis] == self.gp_sample, 1., 0.))

    @pytest.mark.parametrize('reference_frequency', REFERENCE_FREQUENCIES)
    def test_disperse_reference_frequency(self, reference_frequency):
        disperse = Disperse(self.gp, self.dm,
                            reference_frequency=reference_frequency)
        assert (disperse.samples_per_frame == 32768 - 6400 or
                disperse.samples_per_frame == 32768 - 6401)
        offset = disperse.start_time - self.start_time
        # Start time kept if ref freq equal to lowest frequency.
        expected = self.dm.time_delay(299.936 * u.MHz,
                                      disperse.reference_frequency)
        assert abs(offset - expected) < 1./self.sample_rate

    @pytest.mark.parametrize('reference_frequency', REFERENCE_FREQUENCIES)
    def test_dedisperse_reference_frequency(self, reference_frequency):
        dedisperse = Dedisperse(self.gp, self.dm,
                                reference_frequency=reference_frequency)
        assert (dedisperse.samples_per_frame == 32768 - 6400 or
                dedisperse.samples_per_frame == 32768 - 6401)
        offset = dedisperse.start_time - self.start_time
        # Start time kept if ref freq equal to highest frequency.
        expected = -self.dm.time_delay(300.064 * u.MHz,
                                       dedisperse.reference_frequency)
        assert abs(offset - expected) < 1./self.sample_rate

    @pytest.mark.parametrize('reference_frequency', REFERENCE_FREQUENCIES)
    def test_disperse(self, reference_frequency):
        disperse = Disperse(self.gp, self.dm,
                            reference_frequency=reference_frequency)
        # Seek input time of the giant pulse, corrected to the reference
        # frequency, and read around it.
        t_gp = (self.start_time + self.gp_sample / self.sample_rate +
                self.dm.time_delay(300. * u.MHz,
                                   disperse.reference_frequency))
        disperse.seek(t_gp)
        disperse.seek(-6400 * 5, 1)
        around_gp = disperse.read(6400 * 10)
        # Power in 20 bins of 0.025 s around the giant pulse.
        p = (np.abs(around_gp) ** 2).reshape(-1, 10, 320, 2).sum(2)
        # Note: FT leakage means that not everything outside of the dispersed
        # pulse is zero.  But the total power there is small.
        assert np.all(p[:9].sum(1) < 0.005)
        assert np.all(p[11:].sum(1) < 0.005)
        assert np.all(p[9:11].sum() > 0.99)
        assert np.all(p[9:11] > 0.047)

    @pytest.mark.parametrize('reference_frequency', REFERENCE_FREQUENCIES)
    def test_disperse_roundtrip1(self, reference_frequency):
        self.gp.seek(self.start_time + 0.5 * u.s)
        self.gp.seek(-1024, 1)
        gp = self.gp.read(2048)
        # Set up dispersion as above, and check that one can invert
        disperse = Disperse(self.gp, self.dm,
                            reference_frequency=reference_frequency)
        dedisperse = Dedisperse(disperse, self.dm,
                                reference_frequency=reference_frequency)
        dedisperse.seek(self.start_time + self.gp_sample / self.sample_rate)
        dedisperse.seek(-1024, 1)
        gp_dd = dedisperse.read(2048)
        # Note: rounding errors mean this doesn't work perfectly.
        assert np.all(np.abs(gp_dd - gp) < 1.e-4)

    @pytest.mark.parametrize('reference_frequency', REFERENCE_FREQUENCIES)
    def test_disperse_roundtrip2(self, reference_frequency):
        # Now check dedispersing using mean frequency, which means that
        # the giant pulse should still be at the dispersed t_gp, i.e., there
        # should be a net time shift as well as a phase shift.
        disperse = Disperse(self.gp, self.dm,
                            reference_frequency=reference_frequency)
        # Seek input time of the giant pulse, corrected to the reference
        # frequency, and read around it.
        time_delay = self.dm.time_delay(300. * u.MHz,
                                        disperse.reference_frequency)
        phase_delay = self.dm.phase_delay(300. * u.MHz,
                                          disperse.reference_frequency)
        t_gp = (self.start_time + self.gp_sample / self.sample_rate +
                time_delay)
        # Dedisperse to mean frequency = 300 MHz, and read dedispersed pulse.
        dedisperse = Dedisperse(disperse, self.dm)
        dedisperse.seek(t_gp)
        dedisperse.seek(-1024, 1)
        dd_gp = dedisperse.read(2048)
        # First check power is concentrated where it should be.
        p = np.abs(dd_gp) ** 2
        assert np.all(p[1024-1:1024+2].sum(0) > 0.9)
        # Now check that, effectively, we just shifted the giant pulse.
        # Read the original giant pulse
        self.gp.seek(0)
        gp = self.gp.read()
        # Shift in time using a phase gradient in the Fourier domain
        # (plus the phase offset between new and old reference frequency).
        ft = np.fft.fft(gp, axis=0)
        freqs = (np.fft.fftfreq(gp.shape[0], 1./self.sample_rate)[:, np.newaxis] *
                 self.gp.sideband)
        ft *= np.exp(((time_delay * freqs * u.cycle - phase_delay) *
                      self.gp.sideband).to_value(u.rad) * -1j)
        gp_exp = np.fft.ifft(ft, axis=0)
        offset = self.gp_sample + int(np.round((time_delay * self.sample_rate).to_value(u.one)))
        assert np.all(np.abs(gp_exp[offset-1024:offset+1024] - dd_gp) < 1e-3)

    def test_disperse_negative_dm(self):
        disperse = Disperse(self.gp, -self.dm)
        disperse.seek(self.start_time + 0.5 * u.s)
        disperse.seek(-6400 * 5, 1)
        around_gp = disperse.read(6400 * 10)
        p = (np.abs(around_gp) ** 2).reshape(-1, 10, 320, 2).sum(2)
        # Note: FT leakage means that not everything outside of the dispersed
        # pulse is zero.  But the total power there is small.
        assert np.all(p[:9].sum(1) < 0.005)
        assert np.all(p[11:].sum(1) < 0.01)
        assert np.all(p[9:11].sum() > 0.99)
        assert np.all(p[9:11] > 0.048)

    @pytest.mark.parametrize('reference_frequency', REFERENCE_FREQUENCIES)
    def test_dedisperse(self, reference_frequency):
        disperse = Disperse(self.gp, self.dm,
                            reference_frequency=reference_frequency)
        dedisperse = Dedisperse(disperse, self.dm,
                                reference_frequency=reference_frequency)
        dedisperse.seek(self.start_time + 0.5 * u.s)
        dedisperse.seek(-6400 * 5, 1)
        data = dedisperse.read(6400 * 10)
        self.gp.seek(self.start_time + 0.5 * u.s)
        self.gp.seek(-6400 * 5, 1)
        expected = self.gp.read(6400 * 10)
        assert np.all(np.abs(data - expected) < 1e-3)

    def test_disperse_real_data(self):
        gp_real = StreamGenerator(self.make_giant_pulse,
                                  shape=self.shape,
                                  start_time=self.start_time,
                                  sample_rate=self.sample_rate,
                                  samples_per_frame=1000,
                                  dtype=np.float32,
                                  frequency=300*u.MHz,
                                  sideband=np.array((1, -1)))
        disperse = Disperse(gp_real, self.dm)
        assert_quantity_allclose(disperse.reference_frequency,
                                 300. * u.MHz)
        disperse.seek(self.start_time + 0.5 * u.s)
        disperse.seek(-6400 * 5, 1)
        around_gp = disperse.read(6400 * 10)
        assert around_gp.dtype == np.float32
        p = (around_gp ** 2).reshape(-1, 3200, 2).sum(1)
        # Note: FT leakage means that not everything outside of the dispersed
        # pulse is zero.  But the total power there is small.
        assert np.all(p[:9] < 0.006)
        assert np.all(p[11:] < 0.006)
        # Lower sideband [1] has lower frequencies and thus is dispersed to later.
        assert p[9, 0] > 0.99 and p[10, 0] < 0.006
        assert p[10, 1] > 0.99 and p[9, 1] < 0.006
        dedisperse = Dedisperse(disperse, self.dm)
        dedisperse.seek(self.start_time + 0.5 * u.s)
        dedisperse.seek(-6400 * 5, 1)
        data = dedisperse.read(6400 * 10)
        gp_real.seek(self.start_time + 0.5 * u.s)
        gp_real.seek(-6400 * 5, 1)
        expected = gp_real.read(6400 * 10)
        assert np.all(np.abs(data - expected) < 1e-3)
