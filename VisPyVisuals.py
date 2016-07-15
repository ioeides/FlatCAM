from vispy.visuals import CompoundVisual, LineVisual, MeshVisual
from vispy.scene.visuals import create_visual_node
from vispy.gloo import set_state
from vispy.geometry.triangulation import Triangulation
from vispy.color import Color
from shapely.geometry import Polygon, LineString, LinearRing
import numpy as np

try:
    from shapely.ops import triangulate
    import Polygon as gpc
except:
    pass


# Add clear_data method to LineVisual
def clear_data(self):
    self._bounds = None
    self._pos = None
    self._changed['pos'] = True
    self.update()

LineVisual.clear_data = clear_data


class ShapeGroup(object):
    def __init__(self, collection):
        self._collection = collection
        self._indexes = []
        self._visible = True

    def add(self, shape, color=None, face_color=None, visible=True, update=False, layer=1, order=0):
        self._indexes.append(self._collection.add(shape, color, face_color, visible, update, layer, order))

    def clear(self, update=False):
        for i in self._indexes:
            self._collection.remove(i, False)

        del self._indexes[:]

        if update:
            self._collection.redraw()

    def redraw(self):
        self._collection.redraw()

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._visible = value
        for i in self._indexes:
            self._collection.data[i]['visible'] = value

        self._collection.redraw()


class ShapeCollectionVisual(CompoundVisual):

    total_segments = 0
    total_tris = 0

    def __init__(self, line_width=1, triangulation='gpc', layers=3, **kwargs):
        self.data = {}
        self.last_key = -1

        self._meshes = [MeshVisual() for _ in range(0, layers)]
        self._lines = [LineVisual(antialias=True) for _ in range(0, layers)]

        self._line_width = line_width
        self._triangulation = triangulation

        visuals = [self._lines[i / 2] if i % 2 else self._meshes[i / 2] for i in range(0, layers * 2)]

        CompoundVisual.__init__(self, visuals, **kwargs)

        for m in self._meshes:
            m.set_gl_state(polygon_offset_fill=True, polygon_offset=(1, 1), cull_face=False)

        for l in self._lines:
            l.set_gl_state(blend=True)

        self.freeze()

    def add(self, shape, color=None, face_color=None, visible=True, update=False, layer=1, order=0):
        self.last_key += 1

        self.data[self.last_key] = {'geometry': shape, 'color': color, 'face_color': face_color,
                                    'visible': visible, 'layer': layer, 'order': order}
        self.update_shape_buffers(self.last_key)

        if update:
            self._update()

        return self.last_key

    def update_shape_buffers(self, key):
        mesh_vertices = []                      # Vertices for mesh
        mesh_tris = []                          # Faces for mesh
        mesh_colors = []                        # Face colors
        line_pts = []                           # Vertices for line
        line_colors = []                        # Line color

        geo, color, face_color = self.data[key]['geometry'], self.data[key]['color'], self.data[key]['face_color']

        if geo is not None and not geo.is_empty:
            simple = geo.simplify(0.01)         # Simplified shape
            pts = []                            # Shape line points
            tri_pts = []                        # Mesh vertices
            tri_tris = []                       # Mesh faces

            if type(geo) == LineString:
                # Prepare lines
                pts = self._linestring_to_segments(np.asarray(simple)).tolist()

            elif type(geo) == LinearRing:
                # Prepare lines
                pts = self._linearring_to_segments(np.asarray(simple)).tolist()

            elif type(geo) == Polygon:
                # Prepare polygon faces
                if face_color is not None:

                    if self._triangulation == 'vispy':
                        # VisPy triangulation
                        # Concatenated arrays of external & internal line rings
                        vertices = self._open_ring(np.asarray(simple.exterior))
                        edges = self._generate_edges(len(vertices))

                        for ints in simple.interiors:
                            v = self._open_ring(np.asarray(ints))
                            edges = np.append(edges, self._generate_edges(len(v)) + len(vertices), 0)
                            vertices = np.append(vertices, v, 0)

                        tri = Triangulation(vertices, edges)
                        tri.triangulate()
                        tri_pts, tri_tris = tri.pts.tolist(), tri.tris.tolist()

                    elif self._triangulation == 'gpc':

                        # GPC triangulation
                        p = gpc.Polygon(np.asarray(simple.exterior))

                        for ints in simple.interiors:
                            q = gpc.Polygon(np.asarray(ints))
                            p -= q

                        for strip in p.triStrip():
                            # Generate tris indexes for triangle strips
                            a = [[x + y for x in range(0, 3)] for y in range(0, len(strip) - 2)]

                            # Append vertices & tris
                            tri_tris += [[x + len(tri_pts) for x in y] for y in a]
                            tri_pts += strip

                # Prepare polygon edges
                if color is not None:
                    pts = self._linearring_to_segments(np.asarray(simple.exterior)).tolist()
                    for ints in simple.interiors:
                        pts += self._linearring_to_segments(np.asarray(ints)).tolist()

            # Appending data for mesh
            if len(tri_pts) > 0 and len(tri_tris) > 0:
                mesh_tris += tri_tris
                mesh_vertices += tri_pts
                mesh_colors += [Color(face_color).rgba] * len(tri_tris)

            # Appending data for line
            if len(pts) > 0:
                line_pts += pts
                line_colors += [Color(color).rgba] * len(pts)

        # Store buffers
        self.data[key]['line_pts'] = line_pts
        self.data[key]['line_colors'] = line_colors
        self.data[key]['mesh_vertices'] = mesh_vertices
        self.data[key]['mesh_tris'] = mesh_tris
        self.data[key]['mesh_colors'] = mesh_colors

    def remove(self, key, update=False):
        self.data.pop(key)
        if update:
            self._update()

    def clear(self, update=False):
        self.data.clear()
        if update:
            self._update()

    def _update(self):
        mesh_vertices = [[] for _ in range(0, len(self._meshes))]       # Vertices for mesh
        mesh_tris = [[] for _ in range(0, len(self._meshes))]           # Faces for mesh
        mesh_colors = [[] for _ in range(0, len(self._meshes))]         # Face colors
        line_pts = [[] for _ in range(0, len(self._lines))]             # Vertices for line
        line_colors = [[] for _ in range(0, len(self._lines))]          # Line color

        # Merge shapes buffers
        for data in self.data.values():
            if data['visible']:
                try:
                    line_pts[data['layer']] += data['line_pts']
                    line_colors[data['layer']] += data['line_colors']
                    mesh_tris[data['layer']] += [[x + len(mesh_vertices[data['layer']])
                                                  for x in y] for y in data['mesh_tris']]
                    mesh_vertices[data['layer']] += data['mesh_vertices']
                    mesh_colors[data['layer']] += data['mesh_colors']
                except Exception as e:
                    print "Data error", e

        # Updating meshes
        for i, mesh in enumerate(self._meshes):
            if len(mesh_vertices[i]) > 0:
                set_state(polygon_offset_fill=False)
                mesh.set_data(np.asarray(mesh_vertices[i]), np.asarray(mesh_tris[i], dtype=np.uint32),
                              face_colors=np.asarray(mesh_colors[i]))
            else:
                mesh.set_data()

            mesh._bounds_changed()

        # Updating lines
        for i, line in enumerate(self._lines):
            if len(line_pts[i]) > 0:
                line.set_data(np.asarray(line_pts[i]), np.asarray(line_colors[i]), self._line_width, 'segments')
            else:
                line.clear_data()

            line._bounds_changed()

        self._bounds_changed()

    def redraw(self):
        self._update()

    @staticmethod
    def _open_ring(vertices):
        return vertices[:-1] if not np.any(vertices[0] != vertices[-1]) else vertices

    @staticmethod
    def _generate_edges(count):
        edges = np.empty((count, 2), dtype=np.uint32)
        edges[:, 0] = np.arange(count)
        edges[:, 1] = edges[:, 0] + 1
        edges[-1, 1] = 0
        return edges

    @staticmethod
    def _linearring_to_segments(arr):
        # Close linear ring
        if np.any(arr[0] != arr[-1]):
            arr = np.concatenate([arr, arr[:1]], axis=0)

        return ShapeCollection._linestring_to_segments(arr)

    @staticmethod
    def _linestring_to_segments(arr):
        return np.asarray(np.repeat(arr, 2, axis=0)[1:-1])


ShapeCollection = create_visual_node(ShapeCollectionVisual)
