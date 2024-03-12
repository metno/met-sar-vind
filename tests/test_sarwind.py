import pytest
import datetime
import numpy as np
from sarwind.sarwind import SARWind


@pytest.mark.unittests
@pytest.mark.sarwind
def testSARWind__set_aux_wind(monkeypatch):
    """ Test that SARWind.set_aux_wind calls functions
    _get_aux_wind_from_str and Nansat.add_band.
    """
    mock1_called = 0

    def mock1(*a):
        nonlocal mock1_called
        mock1_called += 1
        speed = np.array([12, 10])
        dir = np.array([20, 21])
        time = datetime.datetime(2021, 3, 24, 3, 0)
        return speed, dir, time

    mock2_called = 0

    def mock2(*a, **kw):
        nonlocal mock2_called
        mock2_called += 1

    with monkeypatch.context() as mp:
        mp.setattr(SARWind, "_get_aux_wind_from_str", mock1)
        mp.setattr(SARWind, "add_band", mock2)
        mp.setattr(SARWind, "__init__", lambda *a: None)

        n = SARWind()
        # Check that it works with expected input
        assert n.set_aux_wind('path/to/wind_field_file.nc') is None
        # Check that the mock for _get_aux_wind_from_str is called once
        assert mock1_called == 1
        # Check that the mock for add_band is called twice
        assert mock2_called == 2

        # Check that TypeError is raised if input is of wrong type
        with pytest.raises(TypeError) as e:
            n.set_aux_wind(1)

        assert str(e.value) == "wind must be of type string"


@pytest.mark.nbs
@pytest.mark.sarwind
def testSARWind_using_s1EWnc_arome_filenames(sarEW_NBS, arome):
    """ Test that wind is generated from Sentinel-1 data in EW-mode,
    HH-polarization and NBS netCDF file with wind direction from the
    Arome Arctic model.
    """
    w = SARWind(sarEW_NBS, arome)
    assert type(w) == SARWind


@pytest.mark.safe
@pytest.mark.sarwind
def testSARWind_using_s1IWDVsafe_meps_filenames(sarIW_SAFE, arome):
    """ Test that wind is generated from Sentinel-1 data in IW-mode,
    VV-polarization and SAFE based netcdf file, with wind direction
    from MEPS model.
    """
    w = SARWind(sarIW_SAFE, arome)
    assert type(w) == SARWind
