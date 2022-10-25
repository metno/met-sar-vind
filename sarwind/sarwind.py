""" License: This file is part of https://github.com/metno/met-sar-vind
             met-sar-vind is licensed under the Apache-2.0 license
             (https://github.com/metno/met-sar-vind/blob/main/LICENSE).
"""
import sys
import argparse
import warnings
from datetime import datetime
from dateutil.parser import parse

from matplotlib import pyplot as plt
from matplotlib import cm
import numpy as np

from nansat.nansat import Nansat, Domain, _import_mappers
#from nansat import Nansat, Domain, _import_mappers

#from sarwind.cmod5n import cmod5n_inverse
from cmod5n import cmod5n_inverse

class TimeDiffError(Exception):
    pass

def get_nansat(file):
    return Nansat(file)

class SARWind(Nansat, object):
    """
    A class for calculating wind speed from SAR images using CMOD

    Parameters
    -----------
    sar_image : string or Nansat object
                The SAR image as a filename or Nansat object
    wind_direction : int, numpy array, string, Nansat
                Auxiliary wind field information needed to calculate
                SAR wind (must be or have wind direction in degrees):

                - constant wind direction (integer),
                - array of wind directions, same size as the SAR data,
                - the name of a Nansat compatible file containing
                    wind direction information
                - name of a mapper with functionality to find a wind
                    file (online or on local disk) matching the SAR
                    image time [DEFAULT: 'ncep_wind_online']
                - a Nansat object with wind direction.
    pixel_size : float or int
                Grid pixel size in metres
    resample_alg : int
                Resampling algorithm used for reprojecting wind field
                to SAR image
                    -1 : Average,
                     0 : NearestNeighbour
                     1 : Bilinear (default),
                     2 : Cubic,
                     3 : CubicSpline,
                     4 : Lancoz
    force : bool
                Force wind calculation if True

    """

    def __init__(self, sar_image, wind_direction='ncep_wind_online',
                    band_name=None, pixelsize=500, resample_alg=1, force=False, *args, **kwargs):
        if isinstance(sar_image, str):
            super(SARWind, self).__init__(sar_image, *args, **kwargs)
        elif isinstance(sar_image, Nansat):
            super(SARWind, self).from_domain(sar_image, *args, **kwargs)
            self.vrt = sar_image.vrt
            self.mapper = sar_image.mapper
            self.logger = sar_image.logger

        # Check that this is a SAR image with real-valued VV pol NRCS
        #if band_name:
        #    self.sigma0_bandNo = self.get_band_number({'name': band_name})
        #else:
        #    self.sigma0_bandNo = self.get_band_number({
        #        'standard_name':
        #            'surface_backwards_scattering_coefficient_of_radar_wave',
        #        'polarization': 'VV',
        #        'dataType': '6'
        #    })

        self.sigma0_bandNo = self.get_band_number({
            'standard_name':
                'surface_backwards_scattering_coefficient_of_radar_wave',
            'polarization': 'VV',
             'dataType': '6'
        })


        # Set metadata polarisation if sar_image is SAFE format
        if ('.SAFE' in sar_image):
            if ('_1SDH_' in sar_image):
                self.set_metadata('polarisation', 'HHHV')
            elif ('_1SDV_' in sar_image):
                self.set_metadata('polarisation', 'VVVH')
            elif ('_1SSH_'):
                self.set_metadata('polarisation', 'HH')
            elif ('_1SSV_' in sar_image):
                self.set_metadata('polarisation', 'VV')
            else:
                print ('Polarisation unknown')

        self.SAR_image_time = self.time_coverage_start
           # get_time(
           #     self.sigma0_bandNo).replace(tzinfo=None)
        if pixelsize != 'fullres':
            print('Resizing SAR image to ' + str(pixelsize) + ' m pixel size')
            self.resize(pixelsize=pixelsize)

        if not self.has_band('winddirection'):
            self.set_aux_wind(wind_direction, resample_alg=resample_alg,
                    **kwargs)
        # If this is a netcdf file with already calculated windspeed (e.g.
        # created as a SARWind object in order to use the plotting functions),
        # do not recalculate wind
        if not self.has_band('windspeed') and not force:
            self._calculate_wind()

        # Set watermask
        try:
            valid = self.watermask(tps=True)[1]
        except:
            print('Land mask not available')
        else:
            valid[valid==2] = 0
            self.add_band(array=valid, parameters={
                            'name': 'valid',
                            'note': 'All pixels not equal to 1 are invalid',
                            'long_name': 'Valid pixels (covering open water)'
                        })


    def set_aux_wind(self, wind_direction, *args, **kwargs):
        """
        Add auxiliary wind direction as a band with source information in the
        global metadata.

        Parameters
        -----------
        wind_direction : int, numpy array, string, Nansat
                    Auxiliary wind field information needed to calculate
                    SAR wind (must be or have wind direction in degrees):

                    - constant wind direction (integer),
                    - array of wind directions, same size as the SAR data,
                    - the name of a Nansat compatible file containing
                        wind direction information
                    - name of a mapper with functionality to find a wind
                        file (online or on local disk) matching the SAR
                        image time [DEFAULT: 'ncep_wind_online']
                    - a Nansat object with wind direction.
        resample_alg : int
                    Resampling algorithm used for reprojecting wind field
                    to SAR image
                        -1 : Average,
                         0 : NearestNeighbour
                         1 : Bilinear (default),
                         2 : Cubic,
                         3 : CubicSpline,
                         4 : Lancoz
        """
        if isinstance(wind_direction, str):
            wdir, wdir_time, wspeed = self._get_aux_wind_from_str(
                                        wind_direction, *args, **kwargs)


        self.add_band(array=wdir, parameters={
                            'wkv': 'wind_from_direction',
                            'name': 'winddirection',
                            'time': wdir_time
                })
        if not wspeed is None:
            self.add_band(array=wspeed, nomem=True, parameters={
                            'wkv': 'wind_speed',
                            'name': 'model_windspeed',
                            'time': wdir_time,
            })

    def _get_aux_wind_from_str(self, aux_wind_source, *args, **kwargs):
        import nansat.nansat
        mnames = [key.replace('mapper_','') for key in
                    nansat.nansat.nansatMappers]
        print(mnames)

        # check if aux_wind_source is like 'ncep_wind_online', i.e. only
        # mapper name is given. By adding the SAR image time stamp, we
        # can then get the data online
        if aux_wind_source in mnames:
            aux_wind_source = aux_wind_source + \
                    datetime.strftime(self.SAR_image_time, ':%Y%m%d%H%M')

        aux = Nansat(aux_wind_source, netcdf_dim={
                'time': np.datetime64(self.SAR_image_time),
                'height2': 10, # height dimension used in AROME arctic
                                    # datasets
                'height3': 10,
            },
            bands = [ # CF standard names of desired bands
                'x_wind_10m',
                'y_wind_10m', # or..:
                'x_wind',
                'y_wind', # or..:
                'eastward_wind',
                'northward_wind',
            ])
        # Set filename of source wind in metadata
        try:
            wind_u_bandNo = aux.get_band_number({
                        'standard_name': 'eastward_wind',
                    })
        except ValueError:
            try:
                wind_u_bandNo = aux.get_band_number({
                        'standard_name': 'x_wind',
                    })
            except:
                wind_u_bandNo = aux.get_band_number({
                        'standard_name': 'x_wind_10m',
                    })

        self.set_metadata('WIND_DIRECTION_SOURCE', aux_wind_source)
        wdir, wdir_time, wspeed = self._get_wind_direction_array(aux,
                                        *args, **kwargs)

        return wdir, wdir_time, wspeed

    def _get_wind_direction_array(self, aux_wind, resample_alg=1, *args,
            **kwargs):
        """
            Reproject wind and return the wind directions, time and speed
        """

        if not isinstance(aux_wind, Nansat):
            raise ValueError('Input parameter must be of type Nansat')

        try:
            eastward_wind_bandNo = aux_wind.get_band_number({
                        'standard_name': 'eastward_wind',
                    })
        except ValueError:
            try:
                x_wind_bandNo = aux_wind.get_band_number({
                        'standard_name': 'x_wind',
                    })
                y_wind_bandNo = aux_wind.get_band_number({
                        'standard_name': 'y_wind',
                    })
            except:
                x_wind_bandNo = aux_wind.get_band_number({
                        'standard_name': 'x_wind_10m',
                    })
                y_wind_bandNo = aux_wind.get_band_number({
                        'standard_name': 'y_wind_10m',
                    })
            # Get azimuth of aux_wind y-axis in radians
            az = aux_wind.azimuth_y()*np.pi/180
            x_wind = aux_wind[x_wind_bandNo]
            y_wind = aux_wind[y_wind_bandNo]
            uu = y_wind*np.sin(az) + x_wind*np.cos(az)
            vv = y_wind*np.cos(az) - x_wind*np.sin(az)
            aux_wind.add_band(array=uu, parameters={'wkv': 'eastward_wind'})
            aux_wind.add_band(array=vv, parameters={'wkv': 'northward_wind'})

        ## Check overlap (this is time consuming and should perhaps be omitted
        ## or done differently..)
        #alatmin, alatmax, alonmin, alonmax = aux_wind.get_min_max_lat_lon()
        #nlatmin, nlatmax, nlonmin, nlonmax = self.get_min_max_lat_lon()
        #assert max(0, min(nlatmax, alatmax) - max(nlatmin, alatmin))>0 and \
        #        max(0, min(nlonmax, alonmax) - max(nlonmin, alonmin))>0, \
        #        'Auxiliary wind field is not overlapping with the SAR image'

        ## Crop wind field to SAR image area of coverage (to avoid issue with
        ## polar stereographic data mentioned in nansat.nansat.Nansat.reproject
        ## comments)
        #aux_wind.crop_lonlat([nlonmin, nlonmax], [nlatmin, nlatmax])

        # Then reproject
        # OBS: issue #29, test:
        # openwind_integration_tests.test_sentinel1.S1Test.test_s1aEW_with_arome_arctic
        aux_wind.reproject(self, resample_alg=resample_alg, tps=True)

        if not 'WIND_DIRECTION_SOURCE' in self.get_metadata().keys():
            self.set_metadata('WIND_DIRECTION_SOURCE', aux_wind.filename)

        # Check time difference between SAR image and wind direction object
        timediff = self.SAR_image_time.replace(tzinfo=None) - \
                parse(aux_wind.get_metadata('time_coverage_start'))

        try:
            hoursDiff = np.abs(timediff.total_seconds()/3600.)
        except: # for < python2.7
            secondsDiff = (timediff.microseconds +
                            (timediff.seconds + timediff.days *
                            24 * 3600) * 10**6) / 10**6
            hoursDiff = np.abs(secondsDiff/3600.)

        print('Time difference between SAR image and wind direction: ' \
                + '%.2f' % hoursDiff + ' hours')
        print('SAR image time: ' + str(self.SAR_image_time))
        print('Wind dir time: ' + str(parse(aux_wind.get_metadata('time_coverage_start'))))
        if hoursDiff > 3:
            warnings.warn('Time difference exceeds 3 hours!')
            if hoursDiff > 12:
                raise TimeDiffError('Time difference is %.f - impossible to ' \
                        'estimate reliable wind field' %hoursDiff)

        # Get band numbers of eastward and northward wind
        eastward_wind_bandNo = aux_wind.get_band_number({
                    'standard_name': 'eastward_wind',
                })
        northward_wind_bandNo = aux_wind.get_band_number({
                    'standard_name': 'northward_wind',
                })

        # Get eastward and northward wind speed components
        uu = aux_wind[eastward_wind_bandNo]
        vv = aux_wind[northward_wind_bandNo]

        if uu is None:
            raise Exception('Could not read wind vectors')
        # 0 degrees meaning wind from North, 90 degrees meaning wind from East
        return np.degrees(np.arctan2(-uu, -vv)), \
                aux_wind.time_coverage_start, \
                np.sqrt(np.power(uu, 2) + np.power(vv, 2))

    def _calculate_wind(self):
        """
            Calculate wind speed from SAR sigma0 in VV polarization
        """
        # Calculate SAR wind with CMOD
        # TODO:
        # - add other CMOD versions than CMOD5
        print('Calculating SAR wind with CMOD...')
        startTime = datetime.now()
        look_dir = self[self.get_band_number({'standard_name':
                'sensor_azimuth_angle'})]

        print('self.sigma0_bandNo:')
        print(self.sigma0_bandNo)
        s0vv = self[self.sigma0_bandNo]

        if ('HH' in self.get_metadata('polarisation')):
            # This is a hack to use another PR model than in the nansat pixelfunctions
            inc = self['incidence_angle']
            # PR from Lin Ren, Jingsong Yang, Alexis Mouche, et al. (2017) [remote sensing]
            PR = np.square(1.+2.*np.square(np.tan(inc*np.pi/180.))) / \
                    np.square(1.+1.3*np.square(np.tan(inc*np.pi/180.)))
            s0hh_band_no = self.get_band_number({
                'standard_name':
                    'surface_backwards_scattering_coefficient_of_radar_wave',
                'polarization': 'HH',
                'dataType': '6'
            })
            s0vv = self[s0hh_band_no]*PR

        windspeed = cmod5n_inverse(s0vv,
                            np.mod(self['winddirection'] - look_dir, 360),
                            self['incidence_angle'])
        print('Calculation time: ' + str(datetime.now() - startTime))

        windspeed[np.where(np.isnan(windspeed))] = np.nan
        windspeed[np.where(np.isinf(windspeed))] = np.nan

        # Add wind speed and direction as bands
        # TODO: make it possible to update existing bands... See
        # https://github.com/nansencenter/nansat/issues/58
        wind_direction_time = self.get_metadata('time', 'winddirection')
        self.add_band(array=windspeed, parameters={
                        'wkv': 'wind_speed',
                        'name': 'windspeed',
                        'time': self.time_coverage_start,
                        'wind_direction_time': wind_direction_time
                })

        # TODO: Replace U and V bands with pixelfunctions
        u = -windspeed*np.sin((180.0 - self['winddirection'])*np.pi/180.0)
        v = windspeed*np.cos((180.0 - self['winddirection'])*np.pi/180.0)
        self.add_band(array=u, parameters={
                            'wkv': 'eastward_wind',
                            'time': wind_direction_time,
        })
        self.add_band(array=v, parameters={
                            'wkv': 'northward_wind',
                            'time': wind_direction_time,
        })

        # set winddir_time to global metadata
        self.set_metadata('winddir_time', str(wind_direction_time))

    def _get_masked_windspeed(self, landmask=True, icemask=True,
            windspeedBand='windspeed'):
        try:
            sar_windspeed = self[windspeedBand]
        except:
            raise ValueError('SAR wind has not been calculated, ' \
                'execute calculate_wind(wind_direction) first.')

        sar_windspeed[sar_windspeed<0] = 0
        palette = cm.get_cmap('jet')

        if landmask:
            try: # Land mask
                sar_windspeed = np.ma.masked_where(
                                    self.watermask(tps=True)[1]==2, sar_windspeed)
                palette.set_bad([.3, .3, .3], 1.0) # Land is masked (bad)
            except:
                print('Land mask not available')

        if icemask:
            try: # Ice mask
                try: # first try local file
                    ice = Nansat('metno_local_hires_seaice_' +
                            self.SAR_image_time.strftime('%Y%m%d'),
                            mapperName='metno_local_hires_seaice')
                except: # otherwise Thredds
                    ice = Nansat('metno_hires_seaice:' +
                            self.SAR_image_time.strftime('%Y%m%d'))
                ice.reproject(self, tps=True)
                iceBandNo = ice.get_band_number(
                    {'standard_name': 'sea_ice_area_fraction'})
                sar_windspeed[ice[iceBandNo]>0] = -1
                palette.set_under('w', 1.0) # Ice is 'under' (-1)
            except:
                print('Ice mask not available')

        return sar_windspeed, palette

    def write_geotiff(self, filename, landmask=True, icemask=True):

        sar_windspeed, palette = self._get_masked_windspeed(landmask, icemask)

        nansat_geotiff = Nansat(array=sar_windspeed, domain=self,
                                parameters = {'name': 'masked_windspeed',
                                              'minmax': '0 20'})

        nansat_geotiff.write_geotiffimage(filename)



    def plot(self, filename=None, numVectorsX = 16, show=True,
            clim=[0,20], maskWindAbove=35,
            windspeedBand='windspeed', winddirBand='winddirection',
            northUp_eastRight=True, landmask=True, icemask=True):
        """ Basic plotting function showing CMOD wind speed
        overlaid vectors in SAR image projection

        parameters
        ----------
        filename : string
        numVectorsX : int
            Number of wind vectors along first dimension
        show : Boolean
        clim : list
            Color limits of the image.
        windspeedBand : string or int
        winddirBand : string or int
        landmask : Boolean
        icemask : Boolean
        maskWindAbove : int

        """

        try:
            sar_windspeed, palette = self._get_masked_windspeed(landmask,
                    icemask, windspeedBand=windspeedBand)
        except:
            raise ValueError('SAR wind has not been calculated, ' \
                'execute calculate_wind(wind_direction) before plotting.')
        sar_windspeed[sar_windspeed>maskWindAbove] = np.nan

        winddirReductionFactor = int(np.round(
                self.vrt.dataset.RasterXSize/numVectorsX))

        winddir_relative_up = 360 - self[winddirBand] + \
                                    self.azimuth_y()
        indX = range(0, self.vrt.dataset.RasterXSize, winddirReductionFactor)
        indY = range(0, self.vrt.dataset.RasterYSize, winddirReductionFactor)
        X, Y = np.meshgrid(indX, indY)
        try: # scaling of wind vector length, if model wind is available
            model_windspeed = self['model_windspeed']
            model_windspeed = model_windspeed[Y, X]
        except:
            model_windspeed = 8*np.ones(X.shape)

        Ux = np.sin(np.radians(winddir_relative_up[Y, X]))*model_windspeed
        Vx = np.cos(np.radians(winddir_relative_up[Y, X]))*model_windspeed

        # Make sure North is up, and east is right
        if northUp_eastRight:
            lon, lat = self.get_corners()
            if lat[0] < lat[1]:
                sar_windspeed = np.flipud(sar_windspeed)
                Ux = -np.flipud(Ux)
                Vx = -np.flipud(Vx)
            if lon[0] > lon[2]:
                sar_windspeed = np.fliplr(sar_windspeed)
                Ux = np.fliplr(Ux)
                Vx = np.fliplr(Vx)

        # Plotting
        figSize = sar_windspeed.shape
        legendPixels = 60.0
        legendPadPixels = 5.0
        legendFraction = legendPixels/figSize[0]
        legendPadFraction = legendPadPixels/figSize[0]
        dpi=100.0

        fig = plt.figure()
        fig.set_size_inches((figSize[1]/dpi, (figSize[0]/dpi)*
                                (1+legendFraction+legendPadFraction)))
        ax = fig.add_axes([0,0,1,1+legendFraction])
        ax.set_axis_off()
        plt.imshow(sar_windspeed, cmap=palette, interpolation='nearest')
        plt.clim(clim)
        cbar = plt.colorbar(orientation='horizontal', shrink=.80,
                     aspect=40,
                     fraction=legendFraction, pad=legendPadFraction)
        cbar.ax.set_ylabel('[m/s]', rotation=0) # could replace m/s by units from metadata
        cbar.ax.yaxis.set_label_position('right')
        # TODO: plotting function should be improved to give
        #       nice results for images of all sized
        ax.quiver(X, Y, Ux, Vx, angles='xy', width=0.004,
                    scale=200, scale_units='width',
                    color=[.0, .0, .0], headaxislength=4)
        if filename is not None:
            fig.savefig(filename, pad_inches=0, dpi=dpi)
        if show:
            plt.show()
        return fig

    def get_bands_to_export(self, bands):
        if not bands:
            bands = [
                    self.get_band_number('U'),
                    self.get_band_number('V'),
                    self.get_band_number('valid'),
                    #self.get_band_number('winddirection'),
                    #self.get_band_number('windspeed'),
                ]
            if self.has_band('model_windspeed'):
                bands.append(self.get_band_number('model_windspeed'))
        return bands

    def export(self, *args, **kwargs):
        bands = kwargs.pop('bands', None)
        # TODO: add name of original file to metadata
        print('self')
        print(self)
        super(SARWind, self).export(bands=self.get_bands_to_export(), *args, **kwargs)

    def export2thredds(self, *args, **kwargs):
        #bands = kwargs.pop('bands', None)
        # TODO: add name of original file to metadata
        super(SARWind, self).export2thredds(*args, **kwargs)

###################################
#    If run from command line
###################################
def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', dest='SAR_filename',
                        required=True, help='SAR image filename')
    parser.add_argument('-w', dest='wind_direction',
            default='ncep_wind_online',
            help='Wind direction filename or constant '
            ' (integer, 0 for wind from North, 90 for wind from East etc.). '
            'Omit this argument for automatic download of NCEP GFS winds.')
    parser.add_argument('-n', dest='netCDF',
            help='Export numerical output to NetCDF file')
    parser.add_argument('-f', dest='figure_filename',
            help='Save wind plot as figure (e.g. PNG or JPEG)')
    parser.add_argument('-p', dest='pixelsize', default=500,
            help='Pixel size for SAR wind calculation (default = 500 m)',
                type=float)
    return parser


if __name__ == '__main__':

    parser = create_parser()
    args = parser.parse_args()

    if args.figure_filename is None and args.netCDF is None:
        raise ValueError('Please add filename of processed figure (-f) or' \
                ' netcdf (-n)')

    # Read SAR image
    sw = SARWind(args.SAR_filename, args.wind_direction, pixelsize=args.pixelsize)

    # Save figure
    if args.figure_filename is not None:
        print('Saving output as figure: ' + args.figure_filename)
        plt = sw.plot(filename=args.figure_filename, show=False)

    # Save as netCDF file
    if args.netCDF is not None:
        print('Saving output to netCDF file: ' + args.netCDF)
        sw.export(args.netCDF, bands=[14, 16])  # Exporting windspeed and dir
