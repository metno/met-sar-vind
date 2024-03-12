""" License: This file is part of https://github.com/metno/met-sar-vind
             met-sar-vind is licensed under the Apache-2.0 license
             (https://github.com/metno/met-sar-vind/blob/main/LICENSE).
"""
import warnings
import pytz
from datetime import datetime
from dateutil.parser import parse
import numpy as np
from nansat.nansat import Nansat
from sarwind.cmod5n import cmod5n_inverse
from matplotlib import pyplot as plt
from matplotlib import cm
import logging
import uuid
import netCDF4

class TimeDiffError(Exception):
    pass


class SARWind(Nansat, object):
    """
    A class for calculating wind speed from SAR images using CMOD

    Parameters
    -----------
    sar_image : string
                The SAR image as a filename
    wind : string
                Filename of wind field dataset. This must be possible to open with Nansat.
    pixelsize : float or int
                Grid pixel size in metres (0 for full resolution)
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

    def __init__(self, sar_image, wind, pixelsize=500, resample_alg=1, *args, **kwargs):

        if not isinstance(sar_image, str) or not isinstance(wind, str):
            raise ValueError('Input parameter for SAR and wind direction must be of type string')

        super(SARWind, self).__init__(sar_image, *args, **kwargs)
        
        print(wind)
        print(sar_image)
        self.set_metadata('wind_filename', wind)
        self.set_metadata('sar_filename', sar_image)

        # If this is a netcdf file with already calculated windspeed
        # do not recalculate wind
        if self.has_band('windspeed'):
            raise Exception('Wind speed already calculated')

        # Get HH pol NRCS (since we don't want to use pixel function generated VV pol)
        try:
            self.sigma0_bandNo = self.get_band_number({
                'standard_name':
                    'surface_backwards_scattering_coefficient_of_radar_wave',
                'polarization': 'HH',
                'dataType': '6'
            })
        except ValueError:
            self.sigma0_bandNo = self.get_band_number({
                'standard_name':
                    'surface_backwards_scattering_coefficient_of_radar_wave',
                'polarization': 'VV',
                'dataType': '6'
            })

        print('Resizing SAR image to ' + str(pixelsize) + ' m pixel size')
        self.resize(pixelsize=pixelsize)

        if not self.has_band('wind_direction'):
            self.set_aux_wind(wind, resample_alg=resample_alg, **kwargs)

        self._calculate_wind()

        # Set watermask
        try:
            valid = self.watermask(tps=True)[1]
        except OSError as e:
            warnings.warn(str(e))
        else:
            valid[valid == 2] = 0
            self.add_band(
                array=valid,
                parameters={
                    'name': 'valid',
                    'note': 'All pixels not equal to 1 are invalid',
                    'long_name': 'Valid pixels (covering open water)'})

    def set_aux_wind(self, wind, *args, **kwargs):
        """
        Add auxiliary wind direction as a band with source information in the
        global metadata.

        Parameters
        -----------
        wind : string
                    The name of a Nansat compatible file containing wind direction information
        """
        if type(wind) is not str:
            raise TypeError("wind must be of type string")
        wspeed, wdir, wdir_time = self._get_aux_wind_from_str(wind, *args, **kwargs)

        self.add_band(
            array=wdir,
            parameters={
                'wkv': 'wind_from_direction', 'name': 'winddirection', 'time': wdir_time})
        if wspeed is not None:
            self.add_band(
                array=wspeed,
                nomem=True,
                parameters={'wkv': 'wind_speed', 'name': 'model_windspeed', 'time': wdir_time})

    def _get_aux_wind_from_str(self, aux_wind_source, *args, **kwargs):
        """ Get wind field from a file (aux_wind_source) that can be
        opened with Nansat.
        """
        import nansat.nansat
        mnames = [key.replace('mapper_', '') for key in nansat.nansat.nansatMappers]
        # check if aux_wind_source is like 'ncep_wind_online', i.e. only
        # mapper name is given. By adding the SAR image time stamp, we
        # can then get the data online
        if aux_wind_source in mnames:
            aux_wind_source = aux_wind_source + \
                datetime.strftime(self.time_coverage_start, ':%Y%m%d%H%M')
        aux = Nansat(
            aux_wind_source,
            netcdf_dim={'time': np.datetime64(self.time_coverage_start)},
            # CF standard names of desired bands
            bands=[
                'x_wind_10m',
                'y_wind_10m',  # or..:
                'x_wind',
                'y_wind',  # or..:
                'eastward_wind',
                'northward_wind'])
        # Set filename of source wind in metadata
        wspeed, wdir, wdir_time = self._get_wind_direction_array(aux, *args, **kwargs)

        return wspeed, wdir, wdir_time

    def _get_wind_direction_array(self, aux_wind, resample_alg=1, *args, **kwargs):
        """ Reproject the wind field and return the wind directions,
        time and speed.
        """
        if not isinstance(aux_wind, Nansat):
            raise ValueError('Input parameter must be of type Nansat')

        # # Crop wind field to SAR image area of coverage (to avoid issue with
        # # polar stereographic data mentioned in nansat.nansat.Nansat.reproject
        # # comments)
        # aux_wind.crop_lonlat([nlonmin, nlonmax], [nlatmin, nlatmax])
        # Reproject
        aux_wind.reproject(self, resample_alg=resample_alg, tps=True)

        if aux_wind.has_band('eastward_wind') is None:
            x_wind_bandNo = aux_wind.get_band_number({'standard_name': 'x_wind'})
            y_wind_bandNo = aux_wind.get_band_number({'standard_name': 'y_wind'})
            mask = aux_wind['swathmask']
            # Get azimuth of aux_wind y-axis in radians
            az = aux_wind.azimuth_y()*np.pi/180
            az[mask == 0] = np.nan
            # Get x direction wind
            x_wind = aux_wind[x_wind_bandNo]
            fvx = float(aux_wind.get_metadata(band_id=x_wind_bandNo, key='_FillValue'))
            x_wind[x_wind == fvx] = np.nan
            x_wind[mask == 0] = np.nan
            # Get y direction wind
            y_wind = aux_wind[y_wind_bandNo]
            fvy = float(aux_wind.get_metadata(band_id=y_wind_bandNo, key='_FillValue'))
            y_wind[y_wind == fvy] = np.nan
            y_wind[mask == 0] = np.nan

            # Get east-/westward wind speeds
            uu = y_wind*np.sin(az) + x_wind*np.cos(az)
            vv = y_wind*np.cos(az) - x_wind*np.sin(az)
            # aux_wind.add_band(array=uu, parameters={'wkv': 'eastward_wind', 'minmax': '-25 25'})
            # aux_wind.add_band(array=vv, parameters={'wkv': 'northward_wind', 'minmax': '-25 25'})

        # Check time difference between SAR image and wind direction object
        wind_time = aux_wind.get_metadata('time_coverage_start')
        timediff = self.time_coverage_start.replace(tzinfo=None) - \
            parse(wind_time).replace(tzinfo=None)

        hoursDiff = np.abs(timediff.total_seconds()/3600.)

        print('Time difference between SAR image and wind direction: %.2f hours' % hoursDiff)
        print('SAR image time: ' + str(self.time_coverage_start))
        print('Wind dir time: ' + str(parse(wind_time)))
        if hoursDiff > 3:
            warnings.warn('Time difference exceeds 3 hours!')
            if hoursDiff > 12:
                raise TimeDiffError('Time difference is %.f - impossible to '
                                    'estimate reliable wind field' % hoursDiff)

        # # Get band numbers of eastward and northward wind
        # eastward_wind_bandNo = aux_wind.get_band_number({'standard_name': 'eastward_wind'})
        # northward_wind_bandNo = aux_wind.get_band_number({'standard_name': 'northward_wind'})

        # # Get mask, and eastward and northward wind speed components
        # mask = aux_wind['swathmask']
        # uu = aux_wind[eastward_wind_bandNo]
        # uu[mask == 0] = np.nan
        # vv = aux_wind[northward_wind_bandNo]
        # vv[mask == 0] = np.nan

        # if uu is None:
        #     raise Exception('Could not read wind vectors')
        # 0 degrees meaning wind from North, 90 degrees meaning wind from East
        # Return wind direction, time, wind speed
        wind_dir = np.degrees(np.arctan2(-uu, -vv))
        wind_speed = np.sqrt(np.power(uu, 2) + np.power(vv, 2))
        return wind_speed, wind_dir, wind_time

    def _calculate_wind(self):
        """ Calculate wind speed from SAR sigma0 in VV polarization.
        """
        # Calculate SAR wind with CMOD
        # TODO:
        # - add other CMOD versions than CMOD5
        print('Calculating SAR wind with CMOD...')
        startTime = datetime.now()
        look_dir = self[self.get_band_number({'standard_name': 'sensor_azimuth_angle'})]

        s0vv = self[self.sigma0_bandNo]

        if self.get_metadata(band_id=self.sigma0_bandNo, key='polarization') == 'HH':
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

        winddir = self['winddirection']
        look_dir[np.isnan(winddir)] = np.nan
        look_relative_wind_direction = np.mod(winddir - look_dir, 360.)
        windspeed = cmod5n_inverse(s0vv, look_relative_wind_direction,
                                   self['incidence_angle'])
        print('Calculation time: ' + str(datetime.now() - startTime))

        windspeed[np.where(np.isnan(windspeed))] = np.nan
        windspeed[np.where(np.isinf(windspeed))] = np.nan

        # Add wind speed and direction as bands
        wind_direction_time = self.get_metadata(key='time', band_id='winddirection')
        self.add_band(
            array=windspeed,
            parameters={
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
        self.add_band(array=v, parameters={'wkv': 'northward_wind', 'time': wind_direction_time})

        # set winddir_time to global metadata
        self.set_metadata('winddir_time', str(wind_direction_time))

        # Update history
        history = ""
        if "history" in self.vrt.dataset.GetMetadata_List():
            history = self.get_metadata("history")
        self.set_metadata("history", history + "%s: %s(%s, %s)" % (
            datetime.now(tz=pytz.UTC).isoformat(),
            "SARWind",
            self.get_metadata('wind_filename'),
            self.get_metadata('sar_filename'))
        )

    def get_bands_to_export(self, bands):
        if not bands:
            bands = [self.get_band_number('winddirection'),
                     self.get_band_number('windspeed'),
                     self.get_band_number('model_windspeed'),
                     self.get_band_number('U'),
                     self.get_band_number('V'),]
        return bands

    def export(self, *args, **kwargs):
        bands = kwargs.pop('bands', None)
        # TODO: add name of original file to metadata

        super(SARWind, self).export(bands=self.get_bands_to_export(bands), *args, **kwargs)

    def plot(self, filename=None, numVectorsX=16, show=True,
             clim=[0, 20], maskWindAbove=35,
             windspeedBand='windspeed', winddirBand='winddirection',
             northUp_eastRight=True, landmask=False, icemask=False):
        try:
            sar_windspeed = self['windspeed']
            palette = cm.get_cmap('jet')
            # sar_windspeed, palette = self._get_masked_windspeed(landmask,
            # icemask, windspeedBand=windspeedBand)
        except:
            raise ValueError('SAR wind has not been calculated,'
                             'execute calculate_wind(wind_direction) before plotting.')
        sar_windspeed[sar_windspeed > maskWindAbove] = np.nan

        winddirReductionFactor = int(np.round(self.vrt.dataset.RasterXSize/numVectorsX))

        winddir_relative_up = 360 - self[winddirBand] + self.azimuth_y()
        indX = range(0, self.vrt.dataset.RasterXSize, winddirReductionFactor)
        indY = range(0, self.vrt.dataset.RasterYSize, winddirReductionFactor)
        X, Y = np.meshgrid(indX, indY)
        try:  # scaling of wind vector length, if model wind is available
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
        dpi = 100.0

        fig = plt.figure()
        fig.set_size_inches((figSize[1]/dpi, (figSize[0]/dpi)*\
                             (1+legendFraction + legendPadFraction)))
        ax = fig.add_axes([0, 0, 1, 1+legendFraction])
        ax.set_axis_off()
        plt.imshow(sar_windspeed, cmap=palette, interpolation='nearest')
        plt.clim(clim)
        cbar = plt.colorbar(orientation='horizontal', shrink=.80,
                            aspect=40, fraction=legendFraction, pad=legendPadFraction)
        cbar.ax.set_ylabel('[m/s]', rotation=0)  # could replace m/s by units from metadata
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


    def export2netcdf(self, history_message='', filename='', bands=None):
        
        if not filename:
                raise ValueError('Please provide a netcdf filename!')
        
        fn = filename
        log_message = 'Exporting merged subswaths to %s' % fn
            
        date_created = datetime.now(tz=pytz.UTC)
        
        # Get metadata
        metadata = self.get_metadata()
                
        # Updata history
        try:
            history = metadata['history']
        except ValueError:
            history = ''
        history = ''
        if not history_message:
            history_message = '%s: %s, SARWind.export2netcdf(%s)' % \
                (datetime.now(tz=pytz.UTC).isoformat(), history,filename.split('/')[-1])
        else:
            history_message = '%s: %s, %s' % \
                (datetime.now(tz=pytz.UTC).isoformat(), history, history_message)
        
        metadata['history'] = history_message
              
        # Get and set dataset id
        if 'id' not in metadata.keys():
            metadata['id'] = str(uuid.uuid4())
        
        # Set global metadata
        sar_filename = metadata['sar_filename'].split('/')[-1]
        metadata['title'] = 'Surface wind derived from %s' % sar_filename
        metadata['title_no'] = 'Overflate vind utledet fra %s' % sar_filename
        metadata['creator_role'] = 'Technical contact'  
        metadata['creator_type'] = 'person' 
        metadata['creator_name'] = 'Morten Wergeland Hansen'
        metadata['creator_email'] = 'mortenwh@met.no' 
        metadata['creator_institution'] = 'Norwegian Meteorological Institute (MET Norway)'
        metadata['contributor_name'] = 'Frode Dinessen' 
        metadata['contributor_role'] = 'Metadata author' 
        metadata['contributor_email'] = 'froded@met.no'
        metadata['contributor_institution'] = 'Norwegian Meteorological Institute (MET Norway)'
        metadata['project'] = 'MET Norway core services (METNCS)'
        metadata['institution'] = 'Norwegian Meteorological Institute (MET NOrway)'
        metadata['publisher_type'] = 'institution'
        metadata['publisher_name'] = 'Norwegian Meteorological Institute'
        metadata['publisher_url'] = 'https://www.met.no/'
        metadata['publisher_email'] = 'csw-services@met.no'
    
        metadata['references'] = 'https://www.researchgate.net/publication/288260682_CMOD5_An_improved_geophysical_model_function_for_ERS_C-band_scatterometry (Scientific publication)'
        metadata['doi'] = '10.1029/2006JC003743'
        metadata['dataset_production_status'] = 'Complete'
        metadata['summary'] = 'Derived wind information based on the SENTINEL-1 C-band synthetic aperture radar mission'
        metadata['summary_no'] = 'Beregnet vindstyrkt og vindretning utledet fra SENTINEL-1 C-band Synthetic Aperture Radar (SAR) mission'
        metadata['platform'] = 'Sentinel-1%s' % sar_filename[2]
        metadata['platform_vocabulary'] = 'https://vocab.met.no/mmd/Platform/Sentinel-1A'
        metadata['instrument'] = 'SAR-C'
        metadata['instrument_vocabulary'] = 'https://vocab.met.no/mmd/Instrument/SAR-C'
        metadata['Conventions'] = 'CF-1.10,ACDD-1.3'
        metadata['keywords'] = 'GCMDSK:Earth Science > Oceans > RADAR backscatter > Wind'
        metadata['keywords'] = 'GCMDSK:Earth Science > Oceans > RADAR backscatter > Wind, GCMDSK:Earth Science > Spectral/Engineering > RADAR > RADAR imagery,' \
            'GCMDLOC:Geographic Region > Northern Hemisphere, GCMDLOC:Vertical Location > Sea Surface, '  \
            'GCMDPROV: Government Agencies-non-US > Norway > NO/MET > Norwegian Meteorological Institute'
        metadata['keywords_vocabulary'] = 'GCMDSK:GCMD Science Keywords:https://gcmd.earthdata.nasa.gov/kms/concepts/concept_scheme/sciencekeywords,' \
            'GCMDPROV:GCMD Providers:https://gcmd.earthdata.nasa.gov/kms/concepts/concept_scheme/providers,' \
            'GCMDLOC:GCMD Locations:https://gcmd.earthdata.nasa.gov/kms/concepts/concept_scheme/locations'
        
        
        # Get image boundary
        lon,lat= self.get_border()
        boundary = 'POLYGON (('
        for la, lo in list(zip(lat,lon)):
            boundary += '%.2f %.2f, '%(la,lo)
        boundary = boundary[:-2]+'))'
        # Set bounds as (lat,lon) following ACDD convention and EPSG:4326
        metadata['geospatial_bounds'] = boundary
        metadata['geospatial_bounds_crs'] = 'EPSG:4326'
    
        metadata['sar_wind_resource'] = \
            "https://github.com/metno/met-sar-vind"
    
        # Set metadata from dict
        for key, val in metadata.items():
            self.set_metadata(key=key, value=val)
        
        # If all_bands=True, everything is exported. This is
        # useful when not all the bands in the list above have
        # been created
        if not bands:
            # Bands to be exported            
            bands = [self.get_band_number("model_windspeed"),
                     self.get_band_number("windspeed"),
                     self.get_band_number("U"),
                     self.get_band_number("V")
                     ]

        # Export data to netcdf
        logging.debug(log_message)
        self.export(filename=fn, bands=bands)
    
            # # Nansat has filename metadata, which is wrong, and adds GCPs as variables.
            # # Just remove everything.
            # nc = netCDF4.Dataset(fn, 'a')
            # if 'filename' in nc.ncattrs():
            #     nc.delncattr('filename')
            #     tmp = nc.variables.pop("GCPX")
            #     tmp = nc.variables.pop("GCPY")
            #     tmp = nc.variables.pop("GCPZ")
            #     tmp = nc.variables.pop("GCPPixel")
            #     tmp = nc.variables.pop("GCPLine")
            # nc.close()
    
            # # Add netcdf uri to DatasetURIs
            # ncuri = 'file://localhost' + fn
    
            # locked = True
            # while locked:
            #     try:
            #         new_uri, created = DatasetURI.objects.get_or_create(uri=ncuri, dataset=ds)
            #     except OperationalError as oe:
            #         locked = True
            #     else:
            #         locked = False
            # connection.close()
    
            # return new_uri, created
