import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { 
  BookOpen, 
  GraduationCap, 
  Github, 
  Settings, 
  BarChart3, 
  DollarSign, 
  Activity,
  ExternalLink,
  FileText,
  Code,
  Eye
} from 'lucide-react';

interface CommunityLink {
  id: string;
  title: string;
  description: string;
  url: string;
  icon: React.ReactNode;
  category: 'documentation' | 'training' | 'development' | 'monitoring';
}

interface LinkCategory {
  id: string;
  name: string;
  icon: React.ReactNode;
  description: string;
}

const categories: LinkCategory[] = [
  {
    id: 'documentation',
    name: 'Documentation & Knowledge',
    icon: <BookOpen className="w-5 h-5" />,
    description: 'Internal and external documentation resources',
  },
  {
    id: 'training',
    name: 'Training & Learning',
    icon: <GraduationCap className="w-5 h-5" />,
    description: 'Educational resources and certification programs',
  },
  {
    id: 'development',
    name: 'Development & Code',
    icon: <Code className="w-5 h-5" />,
    description: 'Source control, CI/CD, and development tools',
  },
  {
    id: 'monitoring',
    name: 'Monitoring & Observability',
    icon: <Eye className="w-5 h-5" />,
    description: 'Platform monitoring, data observability, and cost management tools',
  },
];

const communityLinks: CommunityLink[] = [
  // Documentation & Knowledge
  {
    id: 'confluence',
    title: 'Confluence',
    description: 'Internal documentation, wikis, and knowledge base',
    url: 'https://confluence.example.com',
    icon: <FileText className="w-6 h-6" />,
    category: 'documentation',
  },
  {
    id: 'databricks-docs',
    title: 'Databricks Documentation',
    description: 'Official Databricks documentation and guides',
    url: 'https://docs.databricks.com',
    icon: <BookOpen className="w-6 h-6" />,
    category: 'documentation',
  },
  
  // Training & Learning
  {
    id: 'databricks-academy',
    title: 'Databricks Academy',
    description: 'Training courses, certifications, and learning resources',
    url: 'https://academy.databricks.com',
    icon: <GraduationCap className="w-6 h-6" />,
    category: 'training',
  },
  
  // Development & Code
  {
    id: 'github',
    title: 'GitHub',
    description: 'Source code repositories and version control',
    url: 'https://github.com/example',
    icon: <Github className="w-6 h-6" />,
    category: 'development',
  },
  {
    id: 'jenkins',
    title: 'Jenkins',
    description: 'CI/CD pipeline management and automation',
    url: 'https://jenkins.example.com',
    icon: <Settings className="w-6 h-6" />,
    category: 'development',
  },
  
  // Monitoring & Observability
  {
    id: 'datadog',
    title: 'Datadog UI',
    description: 'Application performance monitoring and observability',
    url: 'https://datadog.example.com',
    icon: <Activity className="w-6 h-6" />,
    category: 'monitoring',
  },
  {
    id: 'acceldata',
    title: 'Acceldata UI',
    description: 'Data observability and quality monitoring platform',
    url: 'https://acceldata.example.com',
    icon: <BarChart3 className="w-6 h-6" />,
    category: 'monitoring',
  },
  {
    id: 'finout',
    title: 'Finout UI',
    description: 'Cloud cost management and optimization',
    url: 'https://finout.example.com',
    icon: <DollarSign className="w-6 h-6" />,
    category: 'monitoring',
  },
];

export function CommunityLinks() {
  const getLinksByCategory = (categoryId: string) => {
    return communityLinks.filter((link) => link.category === categoryId);
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Community Links</h1>
        <p className="text-gray-600">
          Quick access to essential resources, tools, and documentation
        </p>
      </div>

      {categories.map((category) => {
        const links = getLinksByCategory(category.id);
        if (links.length === 0) return null;

        return (
          <div key={category.id}>
            <div className="flex items-center gap-2 mb-4">
              <div className="text-primary">
                {category.icon}
              </div>
              <div>
                <h2 className="text-xl font-semibold text-gray-900">{category.name}</h2>
                <p className="text-sm text-gray-500">{category.description}</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {links.map((link) => (
                <Card
                  key={link.id}
                  className="hover:shadow-lg transition-shadow cursor-pointer group"
                >
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block"
                  >
                    <CardHeader>
                      <div className="flex items-start justify-between">
                        <div className="p-3 bg-primary/10 rounded-lg text-primary group-hover:bg-primary/20 transition-colors">
                          {link.icon}
                        </div>
                        <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-primary transition-colors" />
                      </div>
                      <CardTitle className="text-lg mt-4">{link.title}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <p className="text-sm text-gray-600">{link.description}</p>
                    </CardContent>
                  </a>
                </Card>
              ))}
            </div>
          </div>
        );
      })}

      {/* Quick Access Section */}
      <Card className="bg-gray-50">
        <CardHeader>
          <CardTitle className="text-lg">Quick Tips</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-gray-600">
          <p>
            • <strong>New to Databricks?</strong> Start with Databricks Academy and Documentation
          </p>
          <p>
            • <strong>Need help with a specific task?</strong> Check Confluence for internal guides and FAQs
          </p>
          <p>
            • <strong>Working on pipelines or infrastructure?</strong> Access Jenkins, Acceldata, and Datadog for monitoring and automation
          </p>
          <p>
            • <strong>Looking for code examples?</strong> Browse GitHub repositories for reusable patterns
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

