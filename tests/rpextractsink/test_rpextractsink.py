"""
Created on June 17 2020

@author: Joan Hérisson
"""

# Generic for test process
from unittest import TestCase

# Specific for tool
from rptools.rpextractsink import genSink
from rr_cache import rrCache

# Specific for tests themselves
from pathlib  import Path
from tempfile import NamedTemporaryFile
from filecmp  import cmp
from os import (
    path as os_path,
    remove
)
from brs_utils import (
    create_logger,
    extract_gz
)
from shutil    import rmtree
from tempfile  import mkdtemp


# Cette classe est un groupe de tests. Son nom DOIT commencer
# par 'Test' et la classe DOIT hériter de unittest.TestCase.
# 'Test_' prefix is mandatory
class Test_rpExtractSink(TestCase):


    data_path = os_path.join(
        os_path.dirname(__file__),
        'data'
    )
    e_coli_model_path_gz = os_path.join(
        data_path,
        'e_coli_model.sbml.gz'
    )

    cache = rrCache(
        ['cid_strc']
    )


    def setUp(self):
        self.logger = create_logger(__name__, 'ERROR')

        # Create persistent temp folder
        # to deflate compressed data file so that
        # it remains reachable outside of this method.
        # Has to remove manually it in tearDown() method 
        self.temp_d = mkdtemp()

        self.e_coli_model_path = extract_gz(
            self.e_coli_model_path_gz,
            self.temp_d
        )


    def tearDown(self):
        rmtree(self.temp_d)


    def test_genSink(self):
        outfile = NamedTemporaryFile(delete=False)
        outfile.close()
        genSink(
            self.cache,
            input_sbml = self.e_coli_model_path,
            output_sink = outfile.name,
            remove_dead_end = False,
            compartment_id = 'MNXC3',
            logger = self.logger
        )
        outfile.close()
        with open(outfile.name, 'r') as test_f:
            test_content = test_f.read()
            with open(
                os_path.join(
                    self.data_path,
                    'output_sink.csv'
                ),
                'r'
            ) as ref_f:
                ref_content = ref_f.read()
                self.assertEqual(test_content, ref_content)
        remove(outfile.name)


    def test_genSink_rmDE(self):
        outfile = NamedTemporaryFile(delete=False)
        outfile.close()
        genSink(
            self.cache,
            input_sbml = self.e_coli_model_path,
            output_sink = outfile.name,
            remove_dead_end = True,
            compartment_id = 'MNXC3'
        )
        outfile.close()
        with open(outfile.name, 'r') as test_f:
            test_content = test_f.read()
            with open(
                os_path.join(
                    self.data_path,
                    'output_sink_woDE.csv'
                ),
                'r'
            ) as ref_f:
                ref_content = ref_f.read()
                self.assertEqual(test_content, ref_content)
        remove(outfile.name)
