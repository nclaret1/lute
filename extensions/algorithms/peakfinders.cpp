#include "peakfinder8.hh"
#include "peakfinder8_v2.hh"

#ifdef __cplusplus
extern "C" {
#endif
    #define PY_SSIZE_T_CLEAN
    #define PY_LIMITED_API 0x03080000 // Minimum version to 3.8.0
    #include "Python.h"
    #ifndef Py_PYTHON_H
      #error "Python headers required"
    #endif
    #include <numpy/arrayobject.h>

    #include <stdio.h>

    #define MODULE_NAME "lute.tasks.algorithms._peakfinders_ext"
    #define MODULE_DOC                                                             \
        "Peakfinder 8 extension.\n\n"                                              \
        "This extension contains an implementation of Cheetah's "                  \
        "'peakfinder8' peak detection\n"                                           \
        "algorithm."
    #define MODULE_PER_INTERPRET_SIZE -1 // Do not support sub-interpreters


    static PyObject* Peakfinder8Exception;
    static PyObject* peakfinder_8(PyObject*, PyObject*, PyObject*);
    static PyObject* peakfinder_8_v2(PyObject*, PyObject*, PyObject*);
    #ifdef PYPEAKFINDER_8_DEBUG
    static void on_free();
    #endif

    PyDoc_STRVAR(
        peakfinder_8_doc,
        R"o(peakfinder_8(max_num_peaks, data, mask, pix_r, asic_nx, asic_ny, nasics_x, \
            nasics_y, adc_thresh, hitfinder_min_snr, hitfinder_min_pix_count, \
            hitfinder_max_pix_count, hitfinder_local_bg_radius)

        Peakfinder8 peak detection.

        This function finds peaks in a detector data frame using the 'peakfinder8'
        strategy from the Cheetah software package. The 'peakfinder8' peak detection
        strategy is described in the following publication:

        A. Barty, R. A. Kirian, F. R. N. C. Maia, M. Hantke, C. H. Yoon, T. A. White,
        and H. N. Chapman, "Cheetah: software for high-throughput reduction and
        analysis of serial femtosecond x-ray diffraction data", J Appl  Crystallogr,
        vol. 47, pp. 1118-1131 (2014).

        Arguments:

            max_num_peaks (:obj:`int`): The maximum number of peaks that will be retrieved
                from each data frame. Additional peaks will be ignored.

            data (:obj:`numpy.ndarray`): The detector data frame on which the peak finding
                must be performed (as an numpy array of float32).

            mask (:obj:`numpy.ndarray`): A numpy array of int8 storing a mask.  The map can
                be used to mark areas of the data frame that must be excluded from the peak
                search.

                * The map must be a numpy array of the same shape as the data frame on
                  which the algorithm will be applied.

                * Each pixel in the map must have a value of either 0, meaning that
                  the corresponding pixel in the data frame should be ignored, or 1,
                  meaning that the corresponding pixel should be included in the
                  search.

                * The map is only used to exclude areas from the peak search: the data
                  is not modified in any way.

            pix_r (:obj:`numpy.ndarray`): A numpy array of float32 with radius information.

                * The array must have the same shape as the data frame on which the
                  algorithm will be applied.

                * Each element of the array must store, for the corresponding pixel in the
                  data frame, the distance in pixels from the origin of the detector
                  reference system (usually the center of the detector).

            asic_nx (:obj:`int`):: The fs size in pixels of each detector panel in the data
                frame.

            asic_ny (:obj:`int`):: The ss size in pixels of each detector panel in the data
                frame.

            nasics_x (:obj:`int`): The number of panels along the fs axis of the data
                frame.

            nasics_y (:obj:`int`): The number of panels along the ss axis of the data
                frame.

            adc_thresh (:obj:`float`):: The minimum ADC threshold for peak detection.

            hitfinder_min_snr (:obj:`float`): The minimum signal-to-noise ratio for peak
                detection.

            hitfinder_min_pix_count (:obj:`int`): The minimum size of a peak in pixels.

            hitfinder_max_pixel_count (:obj:`int`): The maximum size of a peak in pixels.

            local_bg_radius: The radius for the estimation of the local background in
                pixels.

        Returns:

            :obj:`Tuple[int, List[float], List[float], List[float], List[float], \
    List[float], List[float]`: A tuple storing  information about the detected peaks. The
            tuple has the following elements:

                * The first entry stores the number of peaks that were detected in the data
                frame.

                * The second entry is a list storing the fractional fs indexes that locate
                thedetected peaks in the data frame.

                * The third entry is a list storing the fractional ss indexes that locate the
                the detected peaks in the data frame.

                * The fourth entry is a list storing the integrated intensities for the
                detected peaks.

                * The fifth entry is a list storing the number of pixels that make up each
                detected peak.

                * The sixth entry is a list storing, for each peak, the value of the pixel
                with the maximum intensity.

                * The seventh entry is a list storing the signal-to-noise ratio of each
                detected peak.)o"
    );

    PyDoc_STRVAR(
        peakfinder_8_v2_doc,
        R"o(peakfinder_8_v2(max_num_peaks, data, mask, pix_r, adc_thresh, \
            hitfinder_min_snr, hitfinder_min_pix_count, hitfinder_max_pix_count, \
            hitfinder_local_bg_radius)

        Peakfinder8 peak detection.

        This function finds peaks in a detector data frame using the 'peakfinder8'
        strategy from the Cheetah software package. The 'peakfinder8' peak detection
        strategy is described in the following publication:

        A. Barty, R. A. Kirian, F. R. N. C. Maia, M. Hantke, C. H. Yoon, T. A. White,
        and H. N. Chapman, "Cheetah: software for high-throughput reduction and
        analysis of serial femtosecond x-ray diffraction data", J Appl  Crystallogr,
        vol. 47, pp. 1118-1131 (2014).

        This V2 version does not requiring creating a "slab" from the data.

        Args:

            max_num_peaks (int): The maximum number of peaks that will be retrieved
                from each data frame. Additional peaks will be ignored.

            data (npt.NDArray[float]): The detector data frame on which the peak finding
                must be performed (as an numpy array of float32).

            mask (npt.NDArray[int8]): A numpy array of int8 storing a mask.  The map can
                be used to mark areas of the data frame that must be excluded from the peak
                search.

                * The map must be a numpy array of the same shape as the data frame on
                  which the algorithm will be applied.

                * Each pixel in the map must have a value of either 0, meaning that
                  the corresponding pixel in the data frame should be ignored, or 1,
                  meaning that the corresponding pixel should be included in the
                  search.

                * The map is only used to exclude areas from the peak search: the data
                  is not modified in any way.

            pix_r (npt.NDArray[float]): A numpy array of float32 with radius information.

                * The array must have the same shape as the data frame on which the
                  algorithm will be applied.

                * Each element of the array must store, for the corresponding pixel in the
                  data frame, the distance in pixels from the origin of the detector
                  reference system (usually the center of the detector).

            adc_thresh (float): The minimum ADC threshold for peak detection.

            hitfinder_min_snr (float): The minimum signal-to-noise ratio for peak
                detection.

            hitfinder_min_pix_count (float): The minimum size of a peak in pixels.

            hitfinder_max_pixel_count (int): The maximum size of a peak in pixels.

            local_bg_radius (int): The radius for the estimation of the local
                background in pixels.

        Returns:

            peak_com_x (list[float]): Fractional fs indices of peak centers of mass.
                These are within a panel.

            peak_com_y (list[float]): Fractional ss indices of peak centers of mass.
                These are within a panel.

            peak_com_index (list[int]): Indicies for each peak center of mass.

            peak_com_value (list[float]): The integrated intensities of each peak.

            peak_npix (list[float]): The number of pixels making up each peak.

            peak_maxi (list[float]): The maximum intensity pixel value in each peak.

            peak_sigma (list[float]): The standard deviation of each peak.

            peak_snr (list[float]): The signal to noise ratio of each peak.

            peak_panel_index (list[int]): The peak panel indices.)o");

    static PyMethodDef peakfinders_methods[] = {
      {
          "peakfinder_8",
          (PyCFunction)peakfinder_8,
          METH_VARARGS | METH_KEYWORDS, // Support positional and keyword
          peakfinder_8_doc
      },
      {
          "peakfinder_8_v2",
          (PyCFunction)peakfinder_8_v2,
          METH_VARARGS | METH_KEYWORDS,
          peakfinder_8_v2_doc
      },
      {NULL,NULL,0,NULL}
    };

    static struct PyModuleDef peakfinders_module = {
        PyModuleDef_HEAD_INIT,
        MODULE_NAME,
        MODULE_DOC,
        MODULE_PER_INTERPRET_SIZE,
        peakfinders_methods,
        NULL, // m_slots
        NULL, // m_traverse
        NULL, // m_clear
    #ifdef PYPEAKFINDER_8_DEBUG
        on_free
    #else
        NULL
    #endif
    };


    PyMODINIT_FUNC PyInit__peakfinders_ext(void)
    {
        import_array(); // Numpy
        PyObject* m = PyModule_Create(&peakfinders_module);
        if (!m) {
            return NULL;
        }

        Peakfinder8Exception = PyErr_NewException(MODULE_NAME".Peakfinder8Exception", NULL, NULL);
        if (PyModule_AddObject(m, "Peakfinder8Exception", Peakfinder8Exception) < 0) {
            Py_CLEAR(Peakfinder8Exception);
            Py_DECREF(m);
            return NULL;
        }

        return m;
    }

    // Allow parsing by keyword
    static const char* peakfinder_8_kwlist[] = {
        "max_num_peaks",
        "data",
        "mask",
        "pix_r",
        "rstats_num_pix",
        "rstats_pidx",
        "rstats_radius",
        "fast",
        "asic_nx",
        "asic_ny",
        "nasics_x",
        "nasics_y",
        "adc_thresh",
        "hitfinder_min_snr",
        "hitfinder_min_pix_count",
        "hitfinder_max_pix_count",
        "hitfinder_local_bg_radius",
        NULL
    };

    /**
     * Check that a numpy arrays type and dimensions match what is expected.
     * @param arr_obj The pointer to the NumPy Python array object.
     * @param ndim The expected dimensionality. If `-1` is passed, then this
     *             function only checks for the data type.
     * @param type The expected datatype of the array.
     */
    static int is_array_okay(PyObject* arr_obj, int ndim, int type) {
        if (!PyArray_Check(arr_obj)) {
            return 0;
        }
        PyArrayObject* arr = reinterpret_cast<PyArrayObject*>(arr_obj);
        // If passing ndim == -1, we don't check the dimensionality
        bool type_or_dims_dont_match =
            ndim == -1
          ? PyArray_TYPE(arr) != type
          : PyArray_TYPE(arr) != type || PyArray_NDIM(arr) != ndim;
        if (type_or_dims_dont_match) {
            return 0;
        }
        return 1;
    }

    static PyObject* peakfinder_8(PyObject* self, PyObject* args, PyObject* kwargs)
    {
        // Define all the variables - format specifiers left as comments
        // "iOOO"
        int max_num_peaks;                  //i
        PyObject* data_obj = NULL;          //O -> float*
        PyObject* mask_obj = NULL;          //O -> char*
        PyObject* pix_r_obj = NULL;         //O -> float*

        // "iOOi"
        int rstats_num_pix;                 //i
        PyObject* rstats_pidx_obj = NULL;   //O -> int*
        PyObject* rstats_radius_obj = NULL; //O -> int*
        int fast;                           //i

        // "llll"
        long asic_nx;                       //l
        long asic_ny;                       //l
        long nasics_x;                      //l
        long nasics_y;                      //l

        // "fflll"
        float adc_thresh;                   //f
        float hitfinder_min_snr;            //f
        long hitfinder_min_pix_count;       //l
        long hitfinder_max_pix_count;       //l
        long hitfinder_local_bg_radius;     //l

        // Parse by position or keyword
        if (!PyArg_ParseTupleAndKeywords(
                args, kwargs, "iOOOiOOillllfflll", const_cast<char**>(peakfinder_8_kwlist),
                &max_num_peaks,
                &data_obj,
                &mask_obj,
                &pix_r_obj,
                &rstats_num_pix,
                &rstats_pidx_obj,
                &rstats_radius_obj,
                &fast,
                &asic_nx,
                &asic_ny,
                &nasics_x,
                &nasics_y,
                &adc_thresh,
                &hitfinder_min_snr,
                &hitfinder_min_pix_count,
                &hitfinder_max_pix_count,
                &hitfinder_local_bg_radius)) {
            return NULL;
        }

        // Verify arrays are okay.
        float* data = NULL;
        char* mask = NULL;
        float* pix_r = NULL;

        if (!is_array_okay(data_obj, 2, NPY_FLOAT32)) {
            PyErr_SetString(Peakfinder8Exception,
                            "data must be a 2D NumPy array of float32.");
            return NULL;
        } else {
            PyArrayObject* data_arr = reinterpret_cast<PyArrayObject*>(data_obj);
            data = reinterpret_cast<float*>(PyArray_DATA(data_arr));
        }

        if (!is_array_okay(mask_obj, 2, NPY_INT8)) {
            PyErr_SetString(Peakfinder8Exception,
                            "mask must be a 2D NumPy array of int8.");
            return NULL;
        } else {
            PyArrayObject* mask_arr = reinterpret_cast<PyArrayObject*>(mask_obj);
            mask = reinterpret_cast<char*>(PyArray_DATA(mask_arr));
        }

        if (!is_array_okay(pix_r_obj, 2, NPY_FLOAT32)) {
            PyErr_SetString(Peakfinder8Exception,
                          "pix_r must be a 2D NumPy array of float32.");
            return NULL;
        } else {
            PyArrayObject* pix_r_arr = reinterpret_cast<PyArrayObject*>(pix_r_obj);
            pix_r = reinterpret_cast<float *>(PyArray_DATA(pix_r_arr));
        }

        // These arrays are optional and may be passed as None from Python
        // Underlying function expects NULL in that case
        int* rstats_pidx = NULL;
        int* rstats_radius = NULL;
        if (rstats_pidx_obj != Py_None) {
            if (!is_array_okay(rstats_pidx_obj, 1, NPY_INT32)) {
                PyErr_SetString(Peakfinder8Exception,
                                "rstats_pidx must be a 1D NumPy array of int or None.");
                return NULL;
            }
            PyArrayObject* rstats_pidx_arr = reinterpret_cast<PyArrayObject*>(rstats_pidx_obj);
            rstats_pidx = reinterpret_cast<int*>(PyArray_DATA(rstats_pidx_arr));
        }
        if (rstats_radius_obj != Py_None) {
            if (!is_array_okay(rstats_radius_obj, 1, NPY_INT32)) {
                PyErr_SetString(Peakfinder8Exception,
                                "rstats_radius must be a 1D NumPy array of int or None.");
                return NULL;
            }
            PyArrayObject* rstats_radius_arr = reinterpret_cast<PyArrayObject*>(rstats_radius_obj);
            rstats_radius = reinterpret_cast<int*>(PyArray_DATA(rstats_radius_arr));
        }

        // Allocate the object for returning the peaks
        tPeakList peak_list;
        allocatePeakList(&peak_list, max_num_peaks);

        // Call the actual peakfinder
        peakfinder8(&peak_list,
                    data,
                    mask,
                    pix_r,
                    rstats_num_pix,
                    rstats_pidx,
                    rstats_radius,
                    fast,
                    asic_nx,
                    asic_ny,
                    nasics_x,
                    nasics_y,
                    adc_thresh,
                    hitfinder_min_snr,
                    hitfinder_min_pix_count,
                    hitfinder_max_pix_count,
                    hitfinder_local_bg_radius,
                    NULL);

        // Put peaks into the return tuple
        PyObject* result = PyTuple_New(8);
        PyObject* peak_list_x = PyList_New(0);
        PyObject* peak_list_y = PyList_New(0);
        PyObject* peak_list_value = PyList_New(0);
        PyObject* peak_list_index = PyList_New(0);
        PyObject* peak_list_npix = PyList_New(0);
        PyObject* peak_list_maxi = PyList_New(0);
        PyObject* peak_list_sigma = PyList_New(0);
        PyObject* peak_list_snr = PyList_New(0);

        int num_peaks = peak_list.nPeaks;
        if (num_peaks > max_num_peaks) {
            num_peaks = max_num_peaks;
        }

        for (int i = 0; i < num_peaks; i++) {
            PyList_Append(peak_list_x, PyFloat_FromDouble(peak_list.peak_com_x[i]));
            PyList_Append(peak_list_y, PyFloat_FromDouble(peak_list.peak_com_y[i]));
            PyList_Append(peak_list_value, PyFloat_FromDouble(peak_list.peak_totalintensity[i]));
            PyList_Append(peak_list_index, PyLong_FromLong(peak_list.peak_com_index[i]));
            PyList_Append(peak_list_npix, PyFloat_FromDouble(peak_list.peak_npix[i]));
            PyList_Append(peak_list_maxi, PyFloat_FromDouble(peak_list.peak_maxintensity[i]));
            PyList_Append(peak_list_sigma, PyFloat_FromDouble(peak_list.peak_sigma[i]));
            PyList_Append(peak_list_snr, PyFloat_FromDouble(peak_list.peak_snr[i]));
        }

        PyTuple_SetItem(result, 0, peak_list_x);
        PyTuple_SetItem(result, 1, peak_list_y);
        PyTuple_SetItem(result, 2, peak_list_value);
        PyTuple_SetItem(result, 3, peak_list_index);
        PyTuple_SetItem(result, 4, peak_list_npix);
        PyTuple_SetItem(result, 5, peak_list_maxi);
        PyTuple_SetItem(result, 6, peak_list_sigma);
        PyTuple_SetItem(result, 7, peak_list_snr);

        // Free allocated memory
        freePeakList(peak_list);

        return result;
    }

    /**************************************************************************/
    // Peakfinder8 V2 - no "slab" required



    static const char* peakfinder_8_v2_kwlist[] = {
        "max_num_peaks",
        "data",
        "mask",
        "pix_r",
        "adc_thresh",
        "hitfinder_min_snr",
        "hitfinder_min_pix_count",
        "hitfinder_max_pix_count",
        "hitfinder_local_bg_radius",
        NULL
    };

    static PyObject* peakfinder_8_v2(PyObject* self, PyObject* args, PyObject* kwargs)
    {
        // Define all the variables - format specifiers left as comments
        // "iOOO"
        int max_num_peaks;                  //i
        PyObject* data_obj = NULL;          //O -> float*
        PyObject* mask_obj = NULL;          //O -> char*
        PyObject* pix_r_obj = NULL;         //O -> float*

        // "fflll"
        float adc_thresh;                   //f
        float hitfinder_min_snr;            //f
        long hitfinder_min_pix_count;       //l
        long hitfinder_max_pix_count;       //l
        long hitfinder_local_bg_radius;     //l

        // Parse by position or keyword
        if (!PyArg_ParseTupleAndKeywords(
                args, kwargs, "iOOOfflll", const_cast<char**>(peakfinder_8_v2_kwlist),
                &max_num_peaks,
                &data_obj,
                &mask_obj,
                &pix_r_obj,
                &adc_thresh,
                &hitfinder_min_snr,
                &hitfinder_min_pix_count,
                &hitfinder_max_pix_count,
                &hitfinder_local_bg_radius)) {
            return NULL;
        }

        // Verify arrays are okay.
        float* data = NULL;
        char* mask = NULL;
        float* pix_r = NULL;

        std::vector<int> shape;
        if (!is_array_okay(data_obj, -1, NPY_FLOAT32)) {
            PyErr_SetString(Peakfinder8Exception, "data must be an array of float32.");
            return NULL;
        } else {
            PyArrayObject* data_arr = reinterpret_cast<PyArrayObject*>(data_obj);
            data = reinterpret_cast<float*>(PyArray_DATA(data_arr));
            long* shape_ptr = PyArray_SHAPE(data_arr);
            int ndim = PyArray_NDIM(data_arr);
            shape = std::vector<int>(shape_ptr, shape_ptr+ndim);
        }

        if (!is_array_okay(mask_obj, -1, NPY_INT8)) {
            PyErr_SetString(Peakfinder8Exception, "mask must an array of int8.");
            return NULL;
        } else {
            PyArrayObject* mask_arr = reinterpret_cast<PyArrayObject*>(mask_obj);
            mask = reinterpret_cast<char*>(PyArray_DATA(mask_arr));
        }

        if (!is_array_okay(pix_r_obj, -1, NPY_FLOAT32)) {
            PyErr_SetString(Peakfinder8Exception, "pix_r must be an array of float32.");
            return NULL;
        } else {
            PyArrayObject* pix_r_arr = reinterpret_cast<PyArrayObject*>(pix_r_obj);
            pix_r = reinterpret_cast<float *>(PyArray_DATA(pix_r_arr));
        }

        // Allocate the object for returning the peaks
        tPeakList_v2 peak_list;
        allocatePeakList_v2(&peak_list, max_num_peaks);

        // Call the actual peakfinder
        peakfinder8_v2(&peak_list,
                       data,
                       mask,
                       pix_r,
                       shape,
                       adc_thresh,
                       hitfinder_min_snr,
                       hitfinder_min_pix_count,
                       hitfinder_max_pix_count,
                       hitfinder_local_bg_radius);

        // Put peaks into the return tuple
        PyObject* result = PyTuple_New(9);
        PyObject* peak_list_x = PyList_New(0);
        PyObject* peak_list_y = PyList_New(0);
        PyObject* peak_list_panel_num = PyList_New(0);
        PyObject* peak_list_value = PyList_New(0);
        PyObject* peak_list_index = PyList_New(0);
        PyObject* peak_list_npix = PyList_New(0);
        PyObject* peak_list_maxi = PyList_New(0);
        PyObject* peak_list_sigma = PyList_New(0);
        PyObject* peak_list_snr = PyList_New(0);

        int num_peaks = peak_list.nPeaks;
        if (num_peaks > max_num_peaks) {
            num_peaks = max_num_peaks;
        }

        for (int i = 0; i < num_peaks; i++) {
            PyList_Append(peak_list_x, PyFloat_FromDouble(peak_list.peak_com_x[i]));
            PyList_Append(peak_list_y, PyFloat_FromDouble(peak_list.peak_com_y[i]));
            PyList_Append(peak_list_panel_num, PyLong_FromLong(peak_list.peak_panel_number[i]));
            PyList_Append(peak_list_value, PyFloat_FromDouble(peak_list.peak_totalintensity[i]));
            PyList_Append(peak_list_index, PyLong_FromLong(peak_list.peak_com_index[i]));
            PyList_Append(peak_list_npix, PyFloat_FromDouble(peak_list.peak_npix[i]));
            PyList_Append(peak_list_maxi, PyFloat_FromDouble(peak_list.peak_maxintensity[i]));
            PyList_Append(peak_list_sigma, PyFloat_FromDouble(peak_list.peak_sigma[i]));
            PyList_Append(peak_list_snr, PyFloat_FromDouble(peak_list.peak_snr[i]));
        }

        PyTuple_SetItem(result, 0, peak_list_x);
        PyTuple_SetItem(result, 1, peak_list_y);
        PyTuple_SetItem(result, 2, peak_list_value);
        PyTuple_SetItem(result, 3, peak_list_index);
        PyTuple_SetItem(result, 4, peak_list_npix);
        PyTuple_SetItem(result, 5, peak_list_maxi);
        PyTuple_SetItem(result, 6, peak_list_sigma);
        PyTuple_SetItem(result, 7, peak_list_snr);
        PyTuple_SetItem(result, 8, peak_list_panel_num);

        // Free allocated memory
        freePeakList_v2(peak_list);

        return result;
    }

#ifdef PYPEAKFINDER_8_DEBUG
    static void on_free() {
        printf("peakfinder_8 resources released.\n");
    }
#endif
#ifdef __cplusplus
}
#endif
