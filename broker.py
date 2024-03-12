""" License: This file is part of https://github.com/metno/met-sar-vind
             met-sar-vind is licensed under the Apache-2.0 license
             (https://github.com/metno/met-sar-vind/blob/main/LICENSE).
"""
from datetime import datetime, timedelta
from sardata.sardataNBS import SARData
from sarwind.sarwind import SARWind
from winddata.winddata import WINDdata
import warnings
import pytz
import os
import fnmatch
from nansat.nansat import Nansat
import subprocess
import sys
import json

os.environ.setdefault('GDAL_ENABLE_DEPRECATED_DRIVER_DODS','YES')

class Broker():
    """
     This class search for Sentinel-1 data from NBS and Arome-Arctic wind data
     from thredds.
     If matching data is found processing of SAR-wind is started.

     Parameters
     -----------
     endpoint : str
               URL to Data. Default: https://csw.s-enda.k8s.met.no/csw

     bbox     : int list
               A boudary box for search area speified in latitude and longitude
               bbox = [lon_min, lat_min, lon_max, lat_max]

               Example:
               >>> bbox = [-10, 75, 40, 85]

     start    : datetime object
               Specify the start time of the range to serchthe

     stop     :  datetime object
               Specify the stop time of the range to search

               Example:
               >>> from datetime import datetime, timedelta
               >>> stop = datetime(2010, 1, 1, 12, 30, 59).replace(tzinfo=pytz.utc)
               >>> start = stop - timedelta(days=7)

     kw_names : str
               A search string filter to limit the result of a search.
               Example:
               >>> kw_name='Arome-Arctic%'
    """

    def __init__(self, bbox=[-25, 60, 70, 85], start=None, stop=None):
        # Set start and stop value to today if not provided as argument
        if ((not isinstance(start, datetime)) or (not isinstance(stop, datetime))):
            warnings.warn('Start/stop time not provided. Using current time as default!')
            start = datetime.now().replace(tzinfo=pytz.utc)
            stop = start + timedelta(days=1)

        # Reading config.json file
        try:
            json_file = open('./config.json', 'r') 
            conf = json.load(json_file)
        except Exception as e:
                print('Could not reads config.json file')
                print(e)
                sys.exit()    
                
        print('\nSearch for new Sentinel-1 data between start: %s stop: %s' % (start.strftime('%Y%m%dT%H'), stop.strftime('%Y%m%dT%H')))
        self.start = start
        self.stop = stop
        self.outpath = '%s/%04d/%02d/' % (conf['outpath'], start.year,start.month)
        self.scp = conf['scp']
        self.scp_path = conf['scp_path']
        self.scp_host = conf['scp_host']
        self.scp_user = conf['scp_user']
        self.id_rsa = conf['id_rsa']
        
        if not os.path.exists(self.outpath):
            os.makedirs(self.outpath)

        self.sw_filenames = []
        self.nc_filenames = []

        # Find available Sentinel-1 data in NBS
        self.sar_freetext = '%1A_%'
        self.model_freetext = 'Arome-Arctic%'
        self.bbox = bbox
        self.wind = ''
        self.main()
        

    def main(self):
        #  Search NBS for Sentinel-1 data
        sar = SARData(start=self.start, stop=self.stop, bbox=self.bbox, kw_names=self.sar_freetext)
        
        if len(sar.url_opendap) == 0:
            raise Exception('No SAR data available for the requested date/time')

        # Get Arome Arctic data and start the wind generateion
        for sarfile in sar.url_opendap:
            # Check if already processed by looking in the output path
            if (len(self._find_files('%s*' % (os.path.basename(sarfile).split('.')[0]))) > 0):
                print('Already processed: %s' % sarfile)
                continue
            
            # Looking for model wind data
            try:
                modelUrl = self._get_arome_file(sarfile, self.bbox)
                if modelUrl == '':
                    print('Not able to find model wind data matching %s' % (sarfile))
                    print('Move to next sar file \n\n')
                    continue
            except Exception as e:
                print(e)
                modelUrl = ''
                print('Error getting model wind data matching %s' % (sarfile))
                print('Move to next sar file \n\n')
                continue

            # Start running CMOD wind processing
            print('\n Start processing on:')
            print('SAR file: %s' % sarfile)
            print('Model file: %s' % modelUrl)
            try:
                sw = SARWind(sarfile, modelUrl)
                ncfilename = '%s/%s_wind.nc' % (self.outpath, os.path.basename(sarfile).split('.')[0])
                
                #sw.export(ncfilename, bands=[22, 23, 24, 25])
                sw.export2netcdf(filename = ncfilename, bands=None)
                 # sw.plot()
                self.sw_filenames.append(sarfile)
                self.nc_filenames.append(ncfilename)
                
                if self.scp == "True":
                    dstpath = '%s/%04d/%02d/%02d' % (self.scp_path, self.start.year,self.start.month, self.start.day)
                    try:
                        cmd = 'ssh -i %s \"%s@%s\" \"mkdir -p %s\"' % (self.id_rsa, self.scp_user, self.scp_host, dstpath)
                        print(cmd)
                        subprocess.call(cmd, shell=True)
                        
                        cmd = 'scp -i %s %s %s@%s:%s/' % (self.id_rsa, ncfilename, self.scp_user, self.scp_host, dstpath)
                        print(cmd)
                        subprocess.call(cmd, shell=True)
                    except Exception as e:
                        print(e)
            except:
                print('Could not generate wind from files:')
                print('SAR file: %s' % sarfile)
                print('Arome file: %s' % modelUrl) 
         
        # TODO: Keep track of processed files. 
        # For now I am only checking for filename in output netCDF path.
        # Processed files are available from self.nc_filenames

    def _getURL(self, seachStr, url_opendap):
        modelUrl = ''
        for m in url_opendap:
            if (m.find(seachStr) > -1):
                modelUrl = m
                break
        return modelUrl

    def _get_arome_time(self, hour):
        # Get closest %3 hours back in time from input hours
        hour = int(hour)
        if hour%3 == 2:
            time_closest = '%02d' % (hour + (3-hour%3))
        else:
            time_closest = '%02d' % (hour - hour%3)
        return time_closest

    def _get_arome_file(self, sarfile, bbox):
        nobject = Nansat(sarfile)
        ntime = nobject.time_coverage_end
        # Get hour closest to model hour %3 
        hour = self._get_arome_time(ntime.hour)

        # Need to search 4 days ahead in time to find match of model wind
        modelstart = datetime(ntime.year, ntime.month, ntime.day,
                              int(hour), 0, 0).replace(tzinfo=pytz.utc)
        # Get Arome data from previous day in case modelstart data not available 
        modelPrevDay = modelstart - timedelta(days=1)

        modelstop = modelstart + timedelta(days=4)
        
        print('modelstart,modelstop,modelPrevDay')
        print(modelstart, modelstop, modelPrevDay)

        # Find available NWP model file within the start and stop time continue to next SAR file
        model_freetext = '%Arome-Arctic'

        modelwind = WINDdata(start=modelPrevDay, stop=modelstop, kw_names=model_freetext)
        if len(modelwind.url_opendap) == 0:
            raise Exception('No arome data available for date %04d-%02d-%02d' %
                            (modelstart.year, modelstart.month, modelstart.day))

        modelUrl = ''
        cnt = 0
        # Search 3 * 3h back in time to find Arome data closest to SAR file date/time
        while ((modelUrl == '') and (cnt < len(modelwind.url_opendap))):
            previous_time = ((datetime(modelstart.year, modelstart.month, modelstart.day,
                                       modelstart.hour))-\
                             timedelta(hours=cnt*3)).replace(tzinfo=pytz.utc)
            seachStr = 'arome_arctic_det_2_5km_%04d%02d%02dT%02d' % (previous_time.year,
                                                                     previous_time.month,
                                                                     previous_time.day,
                                                                     previous_time.hour)
            modelUrl = self._getURL(seachStr, modelwind.url_opendap)
            cnt += 1
        return modelUrl

    def _find_files(self, pattern):
        '''Return list of files matching pattern in self.outpath folder.'''
        return [n for n in fnmatch.filter(os.listdir(self.outpath), pattern) if
            os.path.isfile(os.path.join(self.outpath, n))]


if __name__ == '__main__':
    start = datetime(2024, 3, 8, 0, 0, 0).replace(tzinfo=pytz.utc)
    stop = datetime(2024, 3, 8, 23, 0, 0).replace(tzinfo=pytz.utc)

    now  = datetime.now().replace(tzinfo=pytz.utc)
    #start = datetime(now.year, now.month, now.day, 0, 0, 0).replace(tzinfo=pytz.utc)
    #stop = datetime(now.year, now.month, now.day, 23, 0, 0).replace(tzinfo=pytz.utc)
    #stop = start + timedelta(days=0)

    broker_object = Broker(start=start, stop=stop)
    # broker_object.wind.plot()  
